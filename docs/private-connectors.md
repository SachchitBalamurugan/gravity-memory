# Publish private sources into a namespace

A connector publishes its selected GitHub path, Notion page tree, or Drive file or folder to everyone who can read the destination namespace. Choose that namespace in the console before adding the connector. A team administrator provisions provider access on the host. A namespace administrator chooses from the provisioned selections and sees sync results in **Connectors**.

## Provision a credential alias

Set `GRAVITY_CONNECTOR_SECRETS` to a JSON file readable by the service and its worker. The supplied Compose configuration mounts the host's `secrets` directory read-only under `/run/gravity` and uses `/run/gravity/connectors.json`. Place referenced private keys in that same mount. Restrict filesystem access to the service account. The service re-reads the file on each sync, so you can rotate credentials or remove an alias without restarting. Namespace databases contain aliases and source selections. Store and restore the secret files separately.

Each alias grants one provider and a list of exact allowed selections to named namespaces. Use the same namespace ID and target in the console. The following file illustrates both GitHub and Notion entries.

```json
{
  "repo-docs": {
    "provider": "github",
    "namespaces": ["engineering"],
    "targets": ["acme/billing/docs"],
    "app_id": "12345",
    "installation_id": "98765",
    "private_key_file": "/run/gravity/gravity-github.pem"
  },
  "team-notes": {
    "provider": "notion",
    "namespaces": ["engineering"],
    "targets": ["11111111-1111-4111-8111-111111111111"],
    "token": "YOUR_NOTION_INTEGRATION_TOKEN"
  }
}
```

### Private GitHub

Create a GitHub App with read access to repository contents and install it on the selected repositories. The service signs an installation JWT with OpenSSL and requests a fresh read-only installation token each sync. If your infrastructure already supplies short-lived installation tokens, use an `installation_token_file` entry pointing to the rotated token instead of the three App fields. On Windows the adapter also recognizes the OpenSSL distributed with Git for Windows. Paste `owner/repo` or `owner/repo/path` into the console. Paths are relative to the default branch and case-sensitive. [GitHub App installation authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation)

Gravity reads the default branch's current tree, walks the selected subtree, and fetches changed UTF-8 text, Markdown, reStructuredText, AsciiDoc, CSV, and TSV blobs by immutable SHA. It ignores symlinks and submodules. A deleted or renamed file disappears from its previous path after a completed sync. [Git tree API](https://docs.github.com/en/rest/git/trees) · [Git blob API](https://docs.github.com/en/rest/git/blobs)

### Notion

Create a read-content integration and connect it to the desired top-level page in Notion. Enter the page ID or page URL in the console. Gravity follows child pages and ordinary nested text blocks, including lists, toggles and table rows. It excludes linked pages, databases, synced blocks and file attachments. Each page has its own source and content digest. Every pass rechecks block membership and titles because a nested edit or revoked child can change the current selection. Unchanged pages reuse their indexed content. [Notion retrieval and pagination](https://developers.notion.com/reference/get-block-children) · [Sharing access with a connection](https://developers.notion.com/docs/authorization)

### Google Drive

Enable the Drive API in Google Cloud, configure OAuth consent, and download a **Desktop app** OAuth client JSON. On a workstation with a browser, run the helper below. It opens Google's consent page, accepts a loopback callback with PKCE and state validation, and saves the resulting alias to a new file. It requests offline, read-only Drive access. Restrict the authorized Google account to the material you want the service to read. Add the alias to the server's secret registry.

```console
python scripts/drive_oauth.py --client google-desktop.json --namespace engineering --target YOUR_DRIVE_FOLDER_ID --alias project-drive --output project-drive-secret.json
```

Enter the same Google Drive folder or file ID or URL in **Connectors**. Folder membership applies recursively, including shared drives. Gravity refreshes access on each sync. It consumes the Drive changes cursor and reconciles current selection membership. A file moved outside the selected folder stops being retrievable. Shortcuts are excluded. Google Docs and Slides export as plain text. Sheets exports the first sheet as CSV. UTF-8 text files are supported. Text-based PDFs require `pypdf` on the server, with up to 200 pages per file. Scanned images require external OCR before import. [Google OAuth offline access](https://developers.google.com/identity/protocols/oauth2/web-server#offline) · [Drive changes feed](https://developers.google.com/workspace/drive/api/guides/manage-changes) · [Google export formats](https://developers.google.com/workspace/drive/api/guides/ref-export-formats)

## Sync, diagnose, and erase

1. In **Connectors**, enter a label, selected root, credential alias, and interval. Choose **Sync** to run the initial import. The shared service also polls registered connectors. Standalone Studio supports manual sync.
2. Check the last synced time, status, document count, and skipped file count. Per-pass limits include 1 MB per body, 10,000 selected entries, 2,000 requests and 48 MB of newly decoded text. Choose narrower roots for larger collections. An incomplete listing, provider rate limit, network failure, or quota error schedules a retry and preserves the last complete checkpoint. Confirmed access losses take effect even if a different part of that sync fails.
3. When the provider confirms loss of root access, Gravity erases the connector's live documents, derived facts and linked traces. Child removals reconcile from the selected membership. Permissions take effect on the next successful provider check. Remove the connector for immediate local erasure.
4. A human hard erase suppresses that upstream item on subsequent syncs, including future edits. Removing and explicitly re-adding a connector creates a fresh publication. Namespace backups taken before erasure can still contain the original material.

## Verification

Run `python -m unittest discover -s tests -p test_private_connectors.py -v` to exercise the adapters, local OAuth callback, GitHub App signing, checkpoint retries, limited credential grants and erasure. The release smoke test read a document from the private Gravity Memory repository using existing GitHub authorization. The first sync imported one source, the second reused it, search returned three passages, and disconnect left zero sources. The GitHub App exchange, Notion, and Google provider replies were tested with fixtures. Live Notion and Google Drive sign-in require provisioned customer accounts.
