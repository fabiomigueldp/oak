# Discord submission

Hi! I have a scoped compatibility investigation for Vanilla 26.3-pre-2 worlds rendered with BlueMap CLI 5.23. I understand this is prerelease support, not a supported-version regression, and your guidelines ask for discussion before a new feature PR.

The relevant observations are compact string/compound palette forms and omitted default block-state properties. In an isolated world copy, normalizing entry shapes **and filling exact-version defaults before applying explicit properties** restored visible terrain and foliage where renaming fields alone was insufficient. Matching 26.3-pre-2 client resources were used.

The report includes two owner-provided post-workaround screenshots (Dappled Forest and a campsite with a straw bed), a small synthetic normalization reference, test limitations, and links to the current deserializer. It contains no private world, raw logs, server configuration, or operational details:

https://github.com/fabiomigueldp/oak/tree/main/docs/bluemap-26.3

This investigation was prepared with AI assistance; I am sharing compatibility evidence, not submitting AI-generated Java code as a PR. Is this useful for the planned 26.3 support work, and is there a preferred existing discussion to attach the findings to?

---

Submission status: published in BlueMap's official `#suggestions` forum on 2026-09-07 UTC (2026-09-06 22:22 America/Sao_Paulo).

Topic: **26.3-pre-2 palette compatibility: omitted defaults and Dappled Forest rendering**

Link: https://discord.com/channels/665868367416131594/1546329678875336754

The published message and topic were verified in the Discord UI. The report and its two unchanged owner-supplied images are linked from the post. This is a compatibility-evidence submission for maintainer discussion, not a code PR, an accepted patch, or a promise of support. No maintainer approval has been recorded.
