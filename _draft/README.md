# Draft gallery

The public root page remains the under-construction placeholder. This directory
contains the future gallery interface.

## Adding a photo

1. Finish editing the original photo.
2. Run `scripts/prepare-photo.sh /path/to/edited-photo.jpg` from the project root.
3. Add the generated file's metadata to `_draft/photos.js` using the included example.

The preparation script creates a web-sized WebP in `_draft/photos/`, strips
embedded metadata, corrects orientation, and permanently adds the
`EndofTimeWorks` watermark as a repeated diagonal pattern. It refuses to
overwrite an existing output.

Do not place full-resolution originals in this repository. The browser overlay
is useful for presentation, but the exported web copy should also contain a
permanent watermark before publication.
