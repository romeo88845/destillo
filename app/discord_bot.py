import os
import sys
import logging
import urllib.request
import urllib.parse
import json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-25s %(levelname)s %(message)s"
)
logger = logging.getLogger("destillo.bot")

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DESTILLO_API = os.environ.get("DESTILLO_API", "http://localhost:8097")

import discord
from discord import app_commands

class DestilloBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Slash commands synced")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID {self.user.id})")


def _api_call(path: str, data: dict = None) -> dict:
    req = urllib.request.Request(
        f"{DESTILLO_API}{path}",
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"},
        method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def _validate_youtube(url: str) -> bool:
    import re
    patterns = [
        r"youtube\.com/watch\?v=[\w-]{11}",
        r"youtu\.be/[\w-]{11}",
        r"youtube\.com/shorts/[\w-]{11}",
        r"youtube\.com/embed/[\w-]{11}",
        r"youtube\.com/live/[\w-]{11}",
    ]
    return any(re.search(p, url) for p in patterns)


def run():
    if not TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set")
        return

    client = DestilloBot()

    @client.tree.command(name="destillo", description="Capture a YouTube video into knowledge base")
    @app_commands.describe(url="YouTube video URL")
    async def destillo_capture(interaction: discord.Interaction, url: str):
        await interaction.response.defer(ephemeral=True)
        if not _validate_youtube(url):
            await interaction.followup.send(
                "That doesn't look like a valid YouTube URL. Try something like:\n"
                "`/destillo https://youtube.com/watch?v=...`",
                ephemeral=True
            )
            return
        result = _api_call("/api/submit", {"url": url})
        if "error" in result:
            await interaction.followup.send(f"Failed: {result['error']}", ephemeral=True)
        elif result.get("id"):
            await interaction.followup.send("Queued for processing.", ephemeral=True)
        else:
            await interaction.followup.send("Already in library.", ephemeral=True)

    @client.tree.command(name="destillo-save", description="Save a YouTube URL to process later")
    @app_commands.describe(url="YouTube video URL")
    async def destillo_save(interaction: discord.Interaction, url: str):
        await interaction.response.defer(ephemeral=True)
        if not _validate_youtube(url):
            await interaction.followup.send(
                "That doesn't look like a valid YouTube URL.",
                ephemeral=True
            )
            return
        result = _api_call("/api/submit", {"url": url, "defer": True})
        if "error" in result:
            await interaction.followup.send(f"Failed: {result['error']}", ephemeral=True)
        elif result.get("id"):
            await interaction.followup.send("Saved for later. Run `/destillo-process` when ready.", ephemeral=True)
        else:
            await interaction.followup.send("Already in library.", ephemeral=True)

    @client.tree.command(name="destillo-process", description="Process all saved (deferred) captures now")
    async def destillo_process(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = _api_call("/api/process-deferred")
        if "error" in result:
            await interaction.followup.send(f"Failed: {result['error']}", ephemeral=True)
        else:
            n = result.get("processed", 0)
            await interaction.followup.send(f"Queued {n} item{'s' if n != 1 else ''} for processing.", ephemeral=True)

    client.run(TOKEN)


if __name__ == "__main__":
    run()
