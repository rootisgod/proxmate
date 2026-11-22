# Publish a Proxmate container release

Pull requests targeting `main`, pushes to `main` and release tags run both release checks.

- **verify** runs the backend and UI suites, builds the Linux amd64 image and tests the bundled guest tooling with networking disabled. This includes the 11 disk-preparation cases against the Windows script shipped in the image. The job also starts a disposable Compose installation twice to check persistence.
- **windows-bootstrap** runs the 18 bootstrap cases on Windows Server 2025 using Windows PowerShell and Pester 3.4.0.

Both checks can run without a Proxmox server or registry credentials.

## Cut a stable release

1. Merge the intended changes into `main` and wait for both jobs in **Container checks and release** to pass on that commit. Check the working tree before tagging. Keep dashboard credentials, local databases, screenshots and ISO media outside the release.
2. Choose an unused stable tag of the form `vMAJOR.MINOR.PATCH`. For example, to publish version 0.6.0 from the verified commit:

   ```sh
   git tag -a v0.6.0 -m "Proxmate 0.6.0"
   git push origin refs/tags/v0.6.0
   ```

3. Follow the tag's **Container checks and release** run in GitHub Actions. On success the workflow pushes the exact tested image to `ghcr.io/<owner>/<repository>:0.6.0`, checks the pull by digest, and attaches `compose.yaml`, `install-proxmox-lxc.sh` and the LXC guide to the GitHub release. The Compose asset pins both the version and SHA-256 digest. The first release, v0.6.0, uses its original Compose asset with the installer linked in the README.
4. From v0.7.0 the workflow also publishes the tested amd64 Docker image archive and stamps its SHA-256, commit and tag into the release installer. After publication, fetch the installer and Compose file without credentials and check the archive against the published hash. GHCR package visibility is managed separately. The installer can use the public release archive if the registry pull fails. Add upgrade notes under `docs/releases/<tag>.md` before tagging. [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

Only the publish job receives `packages: write` and `contents: write`. If publication fails, first check the job log, Actions permissions and the linked package's access for this repository. Rerun failed jobs after correcting a temporary error. When a source change is required, verify it on `main` and cut a new version. Keep tags for published releases on their original commits.

## Check the install path locally

With Docker, Compose and the project's Python dependencies installed:

```sh
docker build --platform linux/amd64 -t proxmate-ci:checked .
python tests/release_install.py proxmate-ci:checked
```

The check uses two disposable Compose projects on loopback ports. It verifies authentication, the read-only ISO mount and credential persistence after recreation. It then imports a portable script and recovers the full dashboard onto a second installation with separate storage, keys and administrator password. It removes both test projects and volumes on exit. Installers use the generated Compose file from the release assets. The checked-in `compose.release.yaml` takes `PROXMATE_IMAGE` so maintainers can run the same checks before publication.
