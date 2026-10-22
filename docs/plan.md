# backup – plan

Status: discussion notes (2026-10-22). Nothing here is implemented yet.

## Raw files of the new tree

restic excludes the .arw files below `Pictures\new` and `Pictures\new-old`, see `config.excludes()`. While
an album is being worked on, its raws are backed up as plain file copies instead. An album is finished when
it moves from `Large\Pictures\Pictures\new` to `Large\Pictures\Originals`, where restic covers everything.

### Tree on the backup drives

The tools (backup and albumflow) own `Backup\`, I own everything else on the drive, e.g. `Backup manual\`.
Only the marker file, `BACKUP-README.txt` and `backup.pyz` live in the root.

    <primary>:\Backup\
      restic-backup-dell-xps-15-home-folder\
      secondary-latest-snapshot.json
      new-raw\                                 name not settled yet (alternatives: raw-originals, new-exchange)
        to-secondary\<topic>\<album>           albumflow copies an album's raws here
        copied-to-secondary\<topic>\<album>    moved here after the copy to the secondary was verified
    <secondary>:\Backup\
      secondary-restic-backup-dell-xps-15-home-folder\
      new-raw\<topic>\<album>

The album trees mirror `Pictures\new\<topic>\<album>`. Today they are flat (`for-backup-disc\<album>`,
`already-on-backup-disc\<album>`), because they were made by hand.

- `copied-to-secondary` on the primary: keeps the raws of the most recent albums.
- `new-raw` on the secondary: keeps an album's raws as long as there is space. Once the album is finished
  it may go, but as late as I can afford.
- `backup update-secondary` is the natural place to copy `to-secondary` to the secondary and move the
  album to `copied-to-secondary` on the primary, since that is when both drives are connected. Moving
  `for-backup-disc` and `already-on-backup-disc` into `Backup\new-raw\` needs albumflow's `BACKUP_DIR`
  changed at the same time.

## To discuss: making room on the drives

Both drives are always more or less full. Before copying an album to the secondary I usually have to delete
an old album there, and the primary's `copied-to-secondary` needs purging too. How to make that convenient?
Probably like albumflow's suggestions for the annex: a list of large and old albums to pick from, rather
than deleting automatically.

Safety rule to consider: an album may only be suggested for deletion from a drive if its raws are covered
elsewhere, e.g. it is finished (in `Originals`) and a snapshot in the secondary restic repo contains it, or
another drive still has a verified copy.
