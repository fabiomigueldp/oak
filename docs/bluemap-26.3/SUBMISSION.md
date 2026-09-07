# Proposed Discord submission

Hi! I have a scoped compatibility investigation for Vanilla 26.3-pre-2 worlds rendered with BlueMap CLI 5.23. I understand this is prerelease support, not a supported-version regression, and your guidelines ask for discussion before a new feature PR.

The relevant observations are compact string/compound palette forms and omitted default block-state properties. In an isolated world copy, normalizing entry shapes **and filling exact-version defaults before applying explicit properties** restored visible terrain and foliage where renaming fields alone was insufficient. Matching 26.3-pre-2 client resources were used.

The report includes two owner-provided post-workaround screenshots (Dappled Forest and a campsite with a straw bed), a small synthetic normalization reference, test limitations, and links to the current deserializer. It contains no private world, raw logs, server configuration, or operational details:

https://github.com/fabiomigueldp/oak/tree/main/docs/bluemap-26.3

This investigation was prepared with AI assistance; I am sharing compatibility evidence, not submitting AI-generated Java code as a PR. Is this useful for the planned 26.3 support work, and is there a preferred existing discussion to attach the findings to?

---

Submission status: prepared; upstream delivery is pending access to an authenticated Discord session. Publication in the Oak repository is not an upstream submission or acceptance.
