"""Preserve the bytes used to build setup media, independently of VM cleanup."""
import io
import json
import zipfile
from xml.etree import ElementTree as ET
from flask import abort, send_file


def initialize(con):
    con.execute('CREATE TABLE IF NOT EXISTS windows_answers (job_id TEXT PRIMARY KEY, value TEXT NOT NULL)')


def save(db, encode, job_id, xml, helper):
    with db() as con:
        con.execute('INSERT INTO windows_answers VALUES (?,?)',
                    (job_id,encode({'xml':xml.decode('utf-8'),'helper':helper})))


def reusable_xml(xml):
    root=ET.fromstring(xml)
    for element in root.iter():
        tag=element.tag.rsplit('}',1)[-1]
        if tag in ('AdministratorPassword','Password','ProductKey'):
            for field in element:
                if field.tag.rsplit('}',1)[-1] in ('Value','Key'):
                    field.text='REPLACE_WITH_PRODUCT_KEY' if tag=='ProductKey' else 'REPLACE_WITH_PASSWORD'
            if len(element)==0: element.text='REPLACE_WITH_PRODUCT_KEY' if tag=='ProductKey' else 'REPLACE_WITH_PASSWORD'
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def download(db,decode,job_id,original):
    with db() as con:
        row=con.execute('SELECT a.value,j.payload FROM windows_answers a JOIN jobs j ON j.id=a.job_id WHERE a.job_id=?',(job_id,)).fetchone()
    if not row: abort(404)
    artifact=decode(row['value'])
    from job_queue import job_config
    payload=decode(row['payload'])
    if original and 'build_credential_revision' in payload.get('plan',{}):
        raise ValueError('Download the reusable answer file for a build using a shared Administrator password.')
    cfg=job_config(payload)
    manifest={'job':job_id,'machine':cfg.get('name'),'source':cfg.get('template',{}).get('name') or cfg.get('filename') or cfg.get('iso'),
              'edition_index':cfg.get('windows',{}).get('image_index') if cfg.get('source_kind')!='template' else None,
              'edition':payload.get('plan',{}).get('edition') if cfg.get('source_kind')!='template' else None,
              'source_kind':cfg.get('source_kind'),'original':original}
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('build.json',json.dumps(manifest,indent=2))
        bundle.writestr('Autounattend.xml',artifact['xml'].encode() if original else reusable_xml(artifact['xml']))
        if artifact['helper']: bundle.writestr('ProxmateBootstrap.ps1',artifact['helper'].encode('utf-8-sig'))
    output.seek(0)
    suffix='original' if original else 'reusable'
    return send_file(output,mimetype='application/zip',as_attachment=True,download_name=f'windows-{job_id}-{suffix}.zip')
