import {basicSetup} from 'codemirror';
import {Compartment} from '@codemirror/state';
import {EditorView, keymap} from '@codemirror/view';
import {StreamLanguage, indentUnit, syntaxHighlighting} from '@codemirror/language';
import {classHighlighter} from '@lezer/highlight';
import {indentWithTab} from '@codemirror/commands';
import {openSearchPanel} from '@codemirror/search';
import {yaml} from '@codemirror/lang-yaml';
import {powerShell} from '@codemirror/legacy-modes/mode/powershell';
import {shell} from '@codemirror/legacy-modes/mode/shell';

const languages = {ansible: yaml(), powershell: StreamLanguage.define(powerShell), shell: StreamLanguage.define(shell)};

export function mount(host, field, kind, update) {
  const language = new Compartment();
  const view = new EditorView({
    parent: host, doc: field.value,
    extensions: [
      EditorView.cspNonce.of(host.ownerDocument.querySelector('link[rel="stylesheet"][nonce]')?.nonce || ''),
      basicSetup, syntaxHighlighting(classHighlighter), language.of(languages[kind]), indentUnit.of('  '),
      keymap.of([indentWithTab]), EditorView.lineWrapping,
      EditorView.contentAttributes.of({'aria-label':'Script source', spellcheck:'false'}),
      EditorView.updateListener.of(change => {
        if (change.docChanged) {
          field.value = change.state.doc.toString();
          field.dispatchEvent(new Event('input', {bubbles:true}));
        }
        if (change.docChanged || change.selectionSet) {
          const position = change.state.selection.main.head;
          const line = change.state.doc.lineAt(position);
          update(line.number, position - line.from + 1);
        }
      })
    ]
  });
  field.hidden = true;
  field.required = false;
  return {
    language: kind => view.dispatch({effects:language.reconfigure(languages[kind])}),
    find: () => openSearchPanel(view),
    locate(line, column) {
      const row = view.state.doc.line(Math.min(line, view.state.doc.lines));
      const pos = Math.min(row.to, row.from + Math.max(0, column - 1));
      view.dispatch({selection:{anchor:pos}, effects:EditorView.scrollIntoView(pos, {y:'center'})});
      view.focus();
    },
    destroy: () => view.destroy()
  };
}
