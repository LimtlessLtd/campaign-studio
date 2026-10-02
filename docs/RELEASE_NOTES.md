# Campaign Studio — public alpha

A local campaign workspace built around maps and their locations. Create or import maps, link NPCs,
items, journals, events and story threads, iterate through reviewed AI proposals, and queue/upload artwork.

Download `campaign-studio-source.zip`, extract it, and follow `README.md`. Python 3.11+ is required. On
Windows use `start.bat`; on macOS/Linux use `sh start.sh`. The ZIP contains application source and development
documentation, with no campaign data, maps, credentials or uploads. Verify it with the accompanying SHA-256.

Foundry integration reads a world manifest and exports scenes/journals with a GM import macro targeting
v12. D&D 5e NPC/item mechanics are descriptions for GM review. Live two-way synchronization and complete
mechanical sheet generation are future work. Optional AI services require their own installation/account.

Back up `DM/data`, `DM/maps` and `DM/uploads` before upgrading. This is alpha software. See the changelog,
architecture review and contribution guide in the download for development plans and current limitations.
