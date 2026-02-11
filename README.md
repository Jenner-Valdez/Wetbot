# Wetbot
Weather Prediction Bot for Kalshi

Main components for this are the scripts.

Don't forget to make an env for the them to run on the terminal and create a new webhook on a separate channel on discord.

To tell the terminal what webhook ULR to use and create the env $env:DISCORD_WEBHOOK_URL="WRITE YOUR ULR HERE"
Confirm this work by sending this in ther terminal afterwards echo $env:DISCORD_WEBHOOK_URL

This will run the program too, also you can change the delta from 0 to 1 to adjust which day you want it to track either current day or new day "tomorrow"
After env runs, runs this command on the terminal python .\kalshi_range_picker.py and it should send the first message saying this works.
