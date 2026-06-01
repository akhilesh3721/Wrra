import os
import discord
from discord.ext import commands
import requests

TOKEN = os.getenv("TOKEN")
PTERO_API = os.getenv("PTERO_API")

PANEL_URL = "https://panel.halix-free.xyz"
SERVER_ID = "83c544fd"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

HEADERS = {
    "Authorization": f"Bearer {PTERO_API}",
    "Accept": "Application/vnd.pterodactyl.v1+json",
    "Content-Type": "application/json"
}

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")

@bot.command()
async def restart(ctx):
    r = requests.post(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/power",
        headers=HEADERS,
        json={"signal": "restart"}
    )

    if r.status_code in [200, 204]:
        await ctx.send("🔄 Server restarting!")
    else:
        await ctx.send(f"❌ Error: {r.status_code}")

@bot.command()
async def start(ctx):
    r = requests.post(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/power",
        headers=HEADERS,
        json={"signal": "start"}
    )

    if r.status_code in [200, 204]:
        await ctx.send("🟢 Server starting!")
    else:
        await ctx.send(f"❌ Error: {r.status_code}")

@bot.command()
async def stop(ctx):
    r = requests.post(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/power",
        headers=HEADERS,
        json={"signal": "stop"}
    )

    if r.status_code in [200, 204]:
        await ctx.send("🔴 Server stopping!")
    else:
        await ctx.send(f"❌ Error: {r.status_code}")

@bot.command()
async def status(ctx):
    r = requests.get(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/resources",
        headers=HEADERS
    )

    if r.status_code == 200:
        state = r.json()["attributes"]["current_state"]
        await ctx.send(f"📊 Server Status: **{state}**")
    else:
        await ctx.send(f"❌ Error: {r.status_code}")

bot.run(TOKEN)
