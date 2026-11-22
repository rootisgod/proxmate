# Publish a Proxmate container release

Pull requests and pushes to `main` run the backend and UI suites, build the Linux amd64 image, exercise the bundled guest tooling without network access and start a disposable Compose installation twice to check persistence. These checks can run without a Proxmox server or registry credentials.

## Cut a stable release

1. Merge the intended changes into `main` and wait for **Container checks and release → verify** to pass on that commit. Check the working tree before tagging. Keep dashboard credentials, local databases, screenshots and ISO media outside the release.
2. Choose an unused stable tag of the form `vMAJOR.MINOR.PATCH`. For example, to publish version 0.6.0 from the verified commit:

   ```sh
   git tag -a v0.6.0 -m "Proxmate 0.6.0"
   git push origin refs/tags/v0.6.0
   ```

3. Follow the tag's **Container checks and release** run in GitHub Actions. On success the workflow pushes the exact tested image to `ghcr.io/<owner>/<repository>:0.6.0`, checks the pull by digest, and attaches `compose.yaml` and the LXC guide to the GitHub release. The Compose asset pins both the version and SHA-256 digest. Each release has its own tag. Use the latest stable GitHub release to find the current installer.
4. For a private rollout, open the package's **Package settings** and confirm its visibility is **Private**. Grant the intended readers access directly or through inherited repository access. A new GHCR container package starts private. The workflow uses GitHub's job token and never changes package visibility. Installation needs access to the release assets and package. [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

Only the publish job receives `packages: write` and `contents: write`. If publication fails, first check the job log, Actions permissions and the linked package's access for this repository. Rerun failed jobs after correcting a temporary error. When a source change is required, verify it on `main` and cut a new version. Keep tags for published releases on their original commits.

## Check the install path locally

With Docker, Compose and the project's Python dependencies installed:

```sh
docker build --platform linux/amd64 -t proxmate-ci:checked .
python tests/release_install.py proxmate-ci:checked
```

The check uses a random Compose project, an unused loopback port and disposable data. It verifies authentication, the read-only ISO mount and that a saved credential survives container recreation. It removes its own test volume on exit. For installers, use the generated Compose file from the release assets. The checked-in `compose.release.yaml` takes `PROXMATE_IMAGE` so maintainers can run the same checks before publication.
