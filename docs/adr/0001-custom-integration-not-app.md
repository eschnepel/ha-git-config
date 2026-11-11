# Custom integration instead of an app (add-on)

The config flow, button entities, services and Repairs issues the feature set needs are integration concepts, whereas an app would need its own ingress UI and Supervisor API plumbing. We build a custom integration (HACS-distributed). It runs inside the Core container, which ships neither `git` nor an `ssh` client, so git and SSH must be pure Python.

Initial target is HA OS on a Raspberry Pi 5 (aarch64); Container and Core installs are deferred.
