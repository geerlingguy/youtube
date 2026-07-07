# Get Sponsor Lists

I've been attempting to build an automated tool to grab a list of all my active Patreon Patrons and GitHub Sponsors for inclusion in my videos.

This folder contains some scripts I've been working on in that regard.

## `Get Sponsor Emails.applescript`

This AppleScript requires me to select all emails in my 'iCloud' inbox, then it will go through and search the titles for strings matching common Patreon + GitHub Sponsors email subject lines, and compile the data into a CSV file.

Then I open the CSV file in Sublime Text and extract the two lists of GitHub/Patreon supporters, to inject into some titles at the end of my video.

I don't like this method, but I've used it for years now, and it allows me to highlight 'newer' supporters.

## `get_github.py` / `get_patreon.py`

These scripts were vibe coded to work with GitHub/Patreon's APIs, and download lists of all active sponsors/patrons.

They output CSV/text files with _all_ sponsors, sorted by tier/amount.

My goal is to have a new end screen / conclusion in my videos which can highlight _all_ active sponsors, every time.

As someone who supports a number of other content creators, I know it's nice to see that support cemented in the video content itself, as a nice acknowledgement / sign of appreciation.

Right now I finally have these scripts working, but am figuring out how to add the data into the video.

Ideally this will be 100% automated, maybe using a Motion or Final Cut Pro plugin, or some transparent PNGs / animated generated video that I can easily overlay.
