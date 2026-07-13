# End Photography uploader

Private photo ingestion service for `end.photography`.

## Processing flow

1. An authenticated administrator uploads an edited image at `/admin/`.
2. The original is stored outside the web root in `/srv/end-photography/originals`.
3. ImageMagick strips metadata, corrects orientation, limits the image to 2400px,
   adds the repeated diagonal `EndofTimeWorks` watermark, and writes a WebP copy.
4. The public copy is stored in `/var/www/end.photography/photos`.
5. Metadata is stored in SQLite and atomically exported to
   `/var/www/end.photography/data/gallery.json`.

Deleting a photo through the admin removes its stored original, public copy,
database row, and manifest entry.

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
