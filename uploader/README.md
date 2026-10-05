# End Photography uploader

Private photo ingestion service for `end.photography`.

## Processing flow

1. An authenticated administrator uploads up to 20 images and optional matching
   Adobe XMP sidecars at `/admin/`.
2. The originals are staged outside the web root. The review screen shows an
   image preview and prefilled metadata fields before anything is published.
3. Confirmed originals are stored in `/srv/end-photography/originals`.
4. ExifTool derives title, caption, keywords, date, location, creator, copyright,
   rating, and label from allowlisted XMP, IPTC, and EXIF fields. Form values take
   precedence, followed by the XMP sidecar and embedded metadata.
5. For camera RAW files, ExifTool extracts the full embedded JPEG preview.
   ImageMagick strips metadata, corrects orientation, limits the image to 2400px,
   adds the repeated diagonal `EndofTimeWorks` watermark, and writes a WebP copy.
6. The public copy is stored in `/var/www/end.photography/photos`.
7. Metadata is stored in SQLite and atomically exported to
   `/var/www/end.photography/data/gallery.json`.

Deleting a photo through the admin removes its stored original, public copy,
database row, and manifest entry.

Standard images are limited to 100 MB. Camera RAW uploads are limited to 1 GB
and use the private Tailscale HTTPS origin so they do not pass through the public
proxy. Supported RAW extensions are `.raw`, `.dng`, `.cr2`, `.cr3`, `.nef`,
`.arw`, `.orf`, `.rw2`, and `.raf`.

Bulk review accepts up to 20 photos, with a 1 GB standard-image batch limit or a
4 GB batch limit when RAW files are included. Staged files older than 24 hours
are removed when the admin is opened.

After a batch publishes, the uploader opens `/admin/preview/`, an authenticated
view of the future public gallery with the real watermarked photos. The public
homepage remains the under-construction placeholder. Nginx also protects the
photo and manifest paths with the existing admin credentials until launch.

RAW previews reflect the preview embedded by the camera or Adobe application.
Adobe development adjustments are not rendered by this service; export JPEG or
TIFF from Adobe when the public copy must exactly match those edits.

## Deployment

- Application: `/opt/end-photography-uploader`
- Environment: `/etc/end-photography-uploader.env`
- Service: `end-photography-uploader.service`
- Listener: `127.0.0.1:8092`
- Public proxy: Nginx `/admin/` on the existing `end.photography` site
- Authentication: Nginx Basic Auth using
  `/etc/nginx/end.photography.htpasswd`

The existing root page remains independent from the uploader.

## Checks

```bash
systemctl status end-photography-uploader.service
curl http://127.0.0.1:8092/admin/healthz
sudo nginx -t
```
