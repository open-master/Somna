# Progressive Disclosure Patterns

Keep SKILL.md body to the essentials and under 500 lines to minimize context bloat. Split content into separate files when approaching this limit. When splitting out content into other files, reference them from SKILL.md and describe clearly when to read them.

**Key principle:** When a skill supports multiple variations, frameworks, or options, keep only the core workflow and selection guidance in SKILL.md. Move variant-specific details into separate reference files.

## Pattern 1: High-level guide with references

```markdown
# PDF Processing

## Quick start

Extract text with pdfplumber.

## Advanced features

- **Form filling**: See [FORMS.md](FORMS.md) for complete guide
- **API reference**: See [REFERENCE.md](REFERENCE.md) for all methods
- **Examples**: See [EXAMPLES.md](EXAMPLES.md) for common patterns
```

Somna loads reference files only when needed.

## Pattern 2: Domain-specific organization

For skills with multiple domains, organize content by domain to avoid loading irrelevant context:

```text
bigquery-skill/
├── SKILL.md
└── references/
    ├── finance.md
    ├── sales.md
    ├── product.md
    └── marketing.md
```

When a user asks about sales metrics, Somna only needs `sales.md`.

## Pattern 3: Conditional details

Show basic content and link to advanced content:

```markdown
# DOCX Processing

## Creating documents

Use docx-js for new documents. See [DOCX-JS.md](DOCX-JS.md).

## Editing documents

For simple edits, modify the XML directly.

**For tracked changes**: See [REDLINING.md](REDLINING.md)
**For OOXML details**: See [OOXML.md](OOXML.md)
```

## Important Guidelines

- **Avoid deeply nested references** - Keep references one level deep from SKILL.md.
- **Structure longer reference files** - For files longer than 100 lines, include a table of contents at the top.
