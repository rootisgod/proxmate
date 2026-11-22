# Proxmate workspace

## Interaction model

An administrator can build a machine, write a guest script or run it on an existing VM independently. A lab composes those same decisions into a portable definition. Saving a lab captures the selected script revisions. Deployment binds the definition to a specific server, resolves credentials and reviews the exact targets.

The process editor takes inspiration from the visible ordering and reusable steps in the [Octopus deployment process](https://octopus.com/docs/projects/deployment-process). Proxmate uses its existing machine roles, dependencies, guest scripts and pack format.

| Destination | Start here to | Completion |
| --- | --- | --- |
| Inventory | Find a VM or template, inspect its connection, clone it or select machines for an operation | Machine details or an operation review |
| Create machine | Pick VM or reusable template, OS, installation source, software and ordered scripts | Save the reviewed build for reuse or confirm creation |
| Script library | Find a compatible script, inspect source and inputs, create a script or edit a saved one | A validated library entry available to builds, runs and labs |
| Run scripts | Choose existing targets and supply the missing guest logins, select scripts or paste a one-off | Review the actual targets and ordered steps, then run |
| Labs | Define machine roles, copy a saved build or machine settings, select steps, order dependencies and inspect saved packs | Save, import or export a definition, then separately map and review a deployment |
| Labs → Deployments | Follow role jobs and inspect the VMs currently using their recorded IDs | Open **Preview cleanup** to see which VMs match the deployment and why each is a candidate or protected |
| ISO library | Discover node and mounted ISO files, identify missing installers and check an individual candidate | Hand a checked source, its node and optional guest tools to Create machine |
| Activity | Follow active jobs, find failures and open per-job results | Inspect the log or use the recovery actions for that job |
| Credentials and Server settings | Manage guest logins and the connection to the hypervisor | Explicitly save the selected settings |

## Workspace colours

Navigation has its own palette in `static/theme.css`. Light mode pairs an ink-blue sidebar with warm ivory. Dark mode pairs an Atlantic-blue sidebar with charcoal. An amber inset marks the current destination, including under More tools. Use the sidebar colour roles for new navigation controls. A distinct navigation surface follows [Carbon’s guidance on inline themes](https://www.carbondesignsystem.com/building-blocks/foundations/color/guidelines).

Proxmate’s contrast targets for the sidebar against the workspace are 7:1 in light mode and 2:1 in dark mode. Navigation text must also reach 4.5:1 on its own background. The UI suite checks these independently. [WCAG text contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)

## Implementation and verification plan

1. Establish the navigation, page headings, local icons, active states and responsive layout. Keep the advanced tools accessible. Primary pages get bookmarkable routes. Machine links retain their server identity.
2. Build from the machine definition and the script definition. The guided wizard owns installation and sizing. The script catalog owns platform, input types and defaults. One picker owns ordered selection, repeats and per-step input values in the three workflows.
3. Compose the lab editor from those definitions. Give existing-machine operations a distinct choice from provisioning a new guest. Editing a saved pack makes a copy using its embedded script definitions. A library edit or deletion must leave the original usable.
4. Distinguish filename discovery from installer inspection. List files for one node, check contents on request and carry the selected source unchanged into the wizard. Keep resource discovery and guest execution off the authoring path.
5. Exercise keyboard navigation, mobile layout, missing and empty states, frozen-pack and credential boundaries, exact media handoff and existing confirmations. Validate working browser controls as well as API responses before publishing.

## Boundaries for future features

Add a machine setting to its authoritative provisioning model and define how it travels in a pack when portable. Add a script input to its catalog schema so each picker receives the same contract. Destination credentials, local media IDs and existing-guest identity are resolved during deployment. An operator can inspect or save a design without starting a job. Reuse local visual components and the established review flow when adding a screen.
