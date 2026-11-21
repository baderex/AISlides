# ADR 0001 · Native PowerPoint export

Use [python-pptx](https://python-pptx.readthedocs.io/en/latest/) to map normalized IR coordinates into OOXML slide geometry. The library produces native text boxes, vector shapes, connectors, tables, embedded pictures and editable charts with spreadsheet data. ReportLab consumes the same intermediate representation for PDF. Reopen exports and assert their native object types and content in automated tests.

Browser and office applications use distinct font engines. Absolute geometry is shared and exact text wrapping can vary with installed fonts. Animations, transitions and custom slide masters are outside the initial IR.

PowerPoint charts and tables use their native styles. Their frame rotation and overall opacity are unsupported in the export adapter. Group geometry renders basic grouping across adapters, while nested rotation and opacity composition differ between the browser, PNG/PDF and Office. The PDF renderer uses local standard fonts for text and substitutes fonts outside its supported families.
