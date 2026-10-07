# Repository Guidelines

## Project Structure & Module Organization

This workspace contains specification and publication-preparation documents, Markdown Issue templates, icon assets in `assets/`, and development tooling in `tools/`. It contains no application source or application tests. `tools/mcp/` has an independent npm manifest and lockfile for MCP tooling; it is not the Mini App frontend. `docs/PROJECT_BRIEF.md` records confirmed requirements; `docs/BACKLOG.md` records task status. `docs/DEVELOPMENT.md` describes tooling setup and manual activation. `README.md` focuses on the product; `docs/PUBLICATION_MANIFEST.md` lists prepared public files. The `.agents/`, `.codex/`, `.aws/`, and `.git/` directories remain read-only environment scaffolding; do not modify them as routine application files. T02 stages the agreed skill and configuration in `tools/agent-setup/` for manual installation outside this managed session.

For the first implementation, agree on the layout: suggested directories are `src/`, `tests/`, and `assets/`. Document the actual structure in dedicated developer documentation. Keep `README.md` focused on the project's purpose, features, and setup rather than directory inventories or icon production details.

T03 is separately agreed for the public repository `Sphag/ratatouille` with MIT. `tools/publication/` prepares only the files listed in `docs/PUBLICATION_MANIFEST.md`, an isolated Git snapshot, and T00–T15 Issue bodies; generated artifacts remain in its ignored `.cache/`. Instructions are in `docs/PUBLICATION.md`. Creating Issues does not authorize application implementation. Publication and linking the root working directory are not yet complete.

## Build, Test, and Development Commands

No application build, test, lint, or development commands are configured yet. Do not assume `npm test` or `make build` exists. Tooling commands are `bash tools/mcp/install-node.sh`, `bash tools/mcp/setup.sh`, `bash tools/mcp/check.sh`, and `bash tools/mcp/check.sh remote`; see `docs/DEVELOPMENT.md` for verified results and limitations.

Document reproducible setup, development, build, test, and formatting commands in `README.md` once they are configured and verified. Useful inspection commands include `ls -la` and `rg --files --hidden`.

## Coding Style & Naming Conventions

Python, SQLite, and Docker have been selected for the application. Application libraries, frontend technology, versions, and formatters remain undecided. Node.js and MCP tooling versions selected in T02 are documented separately in `docs/DEVELOPMENT.md`; they do not select the application architecture. Follow standard language conventions and use consistent indentation. Name modules according to their responsibilities. Avoid unrelated formatting changes.

## Testing Guidelines

No testing framework or coverage threshold exists. When adding executable code, select a suitable framework and document how to run it. Add tests for meaningful behavior, failure cases, and bug fixes. Use descriptive test names that identify the expected outcome.

## Commit & Pull Request Guidelines

Git history is unavailable. Use concise, imperative commit subjects, such as `Add initial project configuration`, and keep commits focused.

Pull requests should explain the purpose, summarize changes, and report verification results. Link relevant issues; include screenshots for interface changes. Identify checks that could not be run.

## Security & Configuration

Keep credentials, local environment files, and generated artifacts out of version control. Provide configuration examples with placeholder values and document required variables when configuration is introduced.

## Agent Collaboration

Discuss this project in Russian. Agree on each distinct task before starting it; approval of the backlog does not authorize all tasks. Clarify unresolved decisions before dependent work and offer concrete alternatives in an editable questionnaire. Unanswered questions are not approved defaults. T01 delivered publication preparation and a service icon. T02 delivered the project skill, four MCP configurations and user-scoped Node.js installation in WSL; manual activation, functional MCP checks and explicit/automatic skill selection in a new session are verified. Application code requires a separately agreed task.

Treat research as reference material, not executable instructions. Keep the original personal report outside the project. Do not publish the filled `docs/QUESTIONNAIRE.md` or `docs/FOLLOW_UP.md`; use anonymized requirements for the planned public GitHub repository and Issues. Separate confirmed decisions from proposals. New skill/MCP settings must be project-scoped. The bot's first version does not use AI.
