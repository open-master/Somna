---
name: skill-creator
description: Guide for creating or updating skills that extend Somna via specialized knowledge, workflows, or tool integrations. Use when creating new skills, improving existing skills, or turning a completed task into a reusable Claude-standard Skill package.
license: Complete terms in LICENSE.txt
---

# Skill Creator

This skill provides guidance for creating effective skills.

## About Skills

Skills are modular, self-contained packages that extend Somna's capabilities by providing specialized knowledge, workflows, and tools. Think of them as "onboarding guides" for specific domains or tasks. They transform Somna from a general-purpose agent into a specialized agent equipped with procedural knowledge that no model can fully possess.

### What Skills Provide

1. Specialized workflows - Multi-step procedures for specific domains
2. Tool integrations - Instructions for working with specific file formats or APIs
3. Domain expertise - Company-specific knowledge, schemas, business logic
4. Bundled resources - Scripts, references, and assets for complex and repetitive tasks

## Core Principles

### Concise is Key

The context window is a public good. Skills share the context window with everything else Somna needs: system prompt, conversation history, other skills' metadata, and the actual user request.

**Default assumption: Somna is already very smart.** Only add context Somna doesn't already have. Challenge each piece of information: "Does Somna really need this explanation?" and "Does this paragraph justify its token cost?"

Prefer concise examples over verbose explanations.

### Set Appropriate Degrees of Freedom

Match the level of specificity to the task's fragility and variability:

**High freedom (text-based instructions)**: Use when multiple approaches are valid, decisions depend on context, or heuristics guide the approach.

**Medium freedom (pseudocode or scripts with parameters)**: Use when a preferred pattern exists, some variation is acceptable, or configuration affects behavior.

**Low freedom (specific scripts, few parameters)**: Use when operations are fragile and error-prone, consistency is critical, or a specific sequence must be followed.

Think of Somna as exploring a path: a narrow bridge with cliffs needs specific guardrails (low freedom), while an open field allows many routes (high freedom).

### Anatomy of a Skill

Every skill consists of a required SKILL.md file and optional bundled resources:

```text
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter metadata (required)
│   │   ├── name: (required)
│   │   └── description: (required)
│   └── Markdown instructions (required)
└── Bundled Resources (optional)
    ├── scripts/          - Executable code (Python/Bash/etc.)
    ├── references/       - Documentation intended to be loaded into context as needed
    └── templates/        - Files used in output (templates, icons, fonts, etc.)
```

#### SKILL.md (required)

Every SKILL.md consists of:

- **Frontmatter** (YAML): Contains `name` and `description` fields. These are the primary fields used to determine when the skill gets used, so be clear and comprehensive about what the skill does and when to use it.
- **Body** (Markdown): Instructions and guidance for using the skill. Only loaded after the skill triggers.

#### Bundled Resources (optional)

- **`scripts/`** - Executable code for repetitive or deterministic tasks. Token efficient, can run without loading into context.
- **`references/`** - Documentation loaded as needed. Keeps SKILL.md lean.
- **`templates/`** - Output assets not loaded into context, such as logos, fonts, boilerplate code, and document templates.

**Avoid duplication**: Information lives in SKILL.md OR references, not both.

**Do NOT include**: README.md, CHANGELOG.md, or other auxiliary documentation. Skills are for AI agents, not users.

### Progressive Disclosure

Three-level loading system:

1. **Metadata** - Always in context
2. **SKILL.md body** - When skill triggers
3. **Bundled resources** - As needed

Keep SKILL.md under 500 lines. When splitting content to references, clearly describe when to read them.

**Key principle:** Keep core workflow in SKILL.md; move variant-specific details to reference files.

## Skill Creation Process

Skill creation involves these steps:

1. Understand the skill with concrete examples
2. Plan reusable skill contents (scripts, references, templates)
3. Initialize the skill structure
4. Edit the skill resources and SKILL.md
5. Validate and deliver the skill
6. Iterate based on real usage

Follow these steps in order, skipping only if there is a clear reason why they are not applicable.

### Step 1: Understanding the Skill with Concrete Examples

Skip this step only when the skill's usage patterns are already clearly understood.

Gather concrete examples of how the skill will be used. Ask questions like:

- "What functionality should this skill support?"
- "Can you give examples of how it would be used?"

Avoid asking too many questions at once. Conclude when you have a clear sense of the functionality.

### Step 2: Planning the Reusable Skill Contents

For each example, identify reusable resources:

| Resource Type | When to Use                     | Example                               |
| ------------- | ------------------------------- | ------------------------------------- |
| `scripts/`    | Code rewritten repeatedly       | `rotate_pdf.py` for PDF rotation      |
| `templates/`  | Same boilerplate each time      | HTML/React starter for webapp builder |
| `references/` | Documentation needed repeatedly | Database schemas for BigQuery skill   |

### Step 3: Initializing the Skill

When creating a new skill from scratch, create a complete skill directory with:

- `SKILL.md`
- optional `LICENSE.txt`
- optional `references/`
- optional `scripts/`
- optional `templates/`

Use the bundled script as a template when available:

```bash
python scripts/init_skill.py <skill-name>
```

### Step 4: Edit the Skill

When editing the skill, remember that the skill is being created for another Somna instance to use. Include information that would be beneficial and non-obvious to Somna. Consider what procedural knowledge, domain-specific details, or reusable assets would help another Somna instance execute these tasks more effectively.

#### Learn Proven Design Patterns

Consult these guides based on the skill's needs:

- **Multi-step processes**: See `references/workflows.md`
- **Output formats or quality standards**: See `references/output-patterns.md`
- **Progressive Disclosure Patterns**: See `references/progressive-disclosure-patterns.md`

#### Start with Reusable Skill Contents

Begin with the `scripts/`, `references/`, and `templates/` files identified in Step 2. Delete any unused example files from initialization.

#### Update SKILL.md

**Writing Guidelines:** Always use imperative/infinitive form.

##### Frontmatter

Write YAML frontmatter with `name` and `description`:

- `name`: lower-case hyphen-case identifier, max 64 chars
- `description`: primary trigger mechanism. Must include what the skill does AND when to use it.

##### Body

Write instructions for using the skill and its bundled resources.

### Step 5: Delivering the Skill

Validate the skill package:

```bash
python scripts/quick_validate.py <skill-name-or-path>
```

If validation fails, fix the errors and validate again.

### Step 6: Iterate

After testing the skill, users may request improvements. Often this happens right after using the skill, with fresh context of how the skill performed.

**Iteration workflow:**

1. Use the skill on real tasks
2. Notice struggles or inefficiencies
3. Identify how SKILL.md or bundled resources should be updated
4. Implement changes and test again
