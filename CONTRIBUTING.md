# Contributing

Use a feature branch and keep changes focused. Install the project in editable
mode, add tests for behavior changes, and run the validation commands documented
in `README.md` before opening a pull request.

Never commit real backup contents, generated reports, credentials, personal
paths, or machine-specific configuration. Use temporary directories and
synthetic data in tests and examples. Preserve backward compatibility for locked
policy schemas unless a migration path is documented.

Pull requests should explain the user-visible behavior, risks to source or
destination data, and validation performed. Destructive behavior requires an
explicit design discussion and must never be introduced as an implicit cleanup.
