# Export reports

1. Open a session in **Tests** or **History** and choose **Export**.
2. Select **HTML report**, **CSV spreadsheet** or **JSON data**.
3. Choose **All tests** or **Working servers only**.
4. Check the result count, choose **Save report**, and select a destination.

**Working servers only** includes Tunnel verified and HTTPS only. Failed, partial, unstable and unfinished tests are excluded. Different protocols, ports, settings and repeat trials remain separate results, even when they use the same server.

The export uses the session as it was when you opened the export dialog. Your tests can continue while the report saves.

## HTML report

Open the saved `.html` file in a current browser with JavaScript enabled. It works offline and does not need external fonts, scripts or a server.

- Search for a server, country, protocol, port or setting.
- Filter by country, protocol or result.
- Click a column heading to sort.
- Open **View** for measurement and settings details.
- Choose **Light theme / Dark theme** to change the appearance.
- Choose **Download filtered CSV** to save matching results.
- Choose **Print / PDF** to open your browser's print options.

CSV download and printing include all matching rows across pages. A working-only report contains only the included results; changing its filters cannot reveal excluded tests.

The summary describes the exported set. The header also shows completed/planned counts for the whole session. **HTTPS only** remains distinct from **Tunnel verified** because the VPN IP could not be confirmed.

## CSV and JSON

CSV opens in spreadsheet applications and includes detailed result columns. JSON contains structured session information and the selected results.

## Before sharing

Reports contain your network/profile labels, notes, server choices and results. They stay on your computer until you share them. Review the contents before posting or sending a report.
