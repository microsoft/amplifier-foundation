---
bundle:
  name: misaligned
  version: 0.0.1
  description: A deliberately misaligned fixture bundle. Not for installation.

tools:
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills
    config:
      skills:
        - "./skills"

agents:
  include:
    - misaligned:fixture-agent
---

@misaligned:context/fixture-awareness.md

@misaligned:context/fixture-operating-rules.md
