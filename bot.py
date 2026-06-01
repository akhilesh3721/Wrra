import discord
from discord.ext import commands
import requests

import os

TOKEN = os.getenv("TOKEN")
PTERO_API = os.getenv("PTERO_API")

PANEL_URL = "https://panel.halix-free.xyz"
SERVER_ID = "83c544fd"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.command()
async def restart(ctx):
    headers = {
        "Authorization": f"Bearer {PTERO_API}",
        "Accept": "Application/vnd.pterodactyl.v1+json",
        "Content-Type": "application/json"
    }

    r = requests.post(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/power",
        headers=headers,
        json={"signal": "restart"}
    )

    if r.status_code in [204, 200]:
        await ctx.send("🔄 Server restarting!")
    else:
        await ctx.send(f"❌ Error: {r.status_code}")

bot.run(TOKEN)
