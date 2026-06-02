import os
import sys
import discord
from discord.ext import commands
import requests
from flask import Flask
from threading import Thread
import asyncio

sys.stdout.reconfigure(line_buffering=True)
app = Flask('')

@app.route('/')
def home():
    return "WRRA BOT ONLINE"

def run_web():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run_web)
    t.start()
TOKEN = os.getenv("TOKEN")
PTERO_API = os.getenv("PTERO_API")

PANEL_URL = "https://panel.halix-free.xyz"
SERVER_ID = "83c544fd"

OWNER_ID = 1365256422585274398  # Your Discord User ID

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

HEADERS = {
    "Authorization": f"Bearer {PTERO_API}",
    "Accept": "Application/vnd.pterodactyl.v1+json",
    "Content-Type": "application/json"
}


def is_owner(ctx):
    return ctx.author.id == OWNER_ID


from mcstatus import JavaServer
from discord.ext import commands, tasks
STATUS_CHANNEL_ID = 1510886773012824176
status_message = None

@tasks.loop(minutes=1)
async def update_status():

    global status_message

    channel = await bot.fetch_channel(STATUS_CHANNEL_ID)

    try:
        server = JavaServer.lookup("free-us4.halix.cloud:19529")
        status = await asyncio.to_thread(server.status)

        embed = discord.Embed(
            title="🟢 WRRA SMP",
            description="⚔️ Survival • Economy • Adventure ⚔️",
            color=0x00ff00
        )

        embed.add_field(
            name="🛰️ Status",
            value="🟢 ONLINE",
            inline=False
        )

        embed.add_field(
            name="👥 Players",
            value=f"{status.players.online}/{status.players.max}",
            inline=False
        )

        embed.add_field(
            name="⚡ Latency",
            value=f"{round(status.latency)}ms",
            inline=False
        )

        embed.add_field(
            name="🔧 Version",
            value="Paper 26.1.2",
            inline=False
        )

    except Exception as e:
        print(f"STATUS ERROR: {e}")

        embed = discord.Embed(
            title="🔴 WRRA SMP",
            description="Server Offline",
            color=0xff0000
        )

    if status_message is None:
        status_message = await channel.send(embed=embed)
    else:
        try:
            await status_message.edit(embed=embed)
        except:
            status_message = await channel.send(embed=embed)
@update_status.error
async def update_status_error(error):
    print(f"UPDATE LOOP CRASHED: {error}")
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    print(f"MESSAGE RECEIVED: {message.content}")
    await bot.process_commands(message)
@bot.event
async def on_command_error(ctx, error):
    print(f"COMMAND ERROR: {error}")
@bot.command()
async def serverstatus(ctx):
    try:
        server = JavaServer.lookup("free-us4.halix.cloud:19529")
        status = await asyncio.to_thread(server.status)

        embed = discord.Embed(
            title="🟢 WRRA SMP",
            color=0x00ff00
        )

        embed.add_field(
            name="🛰 Status",
            value="ONLINE",
            inline=False
        )

        embed.add_field(
            name="👥 Players",
            value=f"{status.players.online}/{status.players.max}",
            inline=False
        )

        embed.add_field(
            name="🔧 Version",
            value="Paper 26.1.2",
            inline=False
        )

        await ctx.send(embed=embed)

    except:
        await ctx.send("🔴 Server Offline")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}", flush=True)
    print("READY EVENT FIRED", flush=True)
    if not update_status.is_running():
        update_status.start()
@bot.command()
async def ping(ctx):
    print("PING COMMAND USED")
    await ctx.send(f"🏓 Pong! {round(bot.latency * 1000)}ms")
@bot.command()
async def help(ctx):
    embed = discord.Embed(
        title="🤖 WRRA Server Bot",
        description="Minecraft Server Management",
        color=0x00ff00
    )

    embed.add_field(
        name="📊 Information",
        value="!ping\n!status",
        inline=False
    )

    embed.add_field(
        name="⚙️ Server Controls",
        value="!start\n!stop\n!restart",
        inline=False
    )

    embed.add_field(
        name="🖥️ Console",
        value="!cmd <command>",
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command()
async def status(ctx):
    r = requests.get(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/resources",
        headers=HEADERS,
        timeout=10
    )

    if r.status_code != 200:
        return await ctx.send(f"❌ Error: {r.status_code}")

    data = r.json()["attributes"]

    state = data["current_state"]
    ram = round(data["resources"]["memory_bytes"] / 1024 / 1024, 2)
    cpu = round(data["resources"]["cpu_absolute"], 2)

    embed = discord.Embed(
        title="📊 Server Status",
        color=0x00ff00
    )

    embed.add_field(name="State", value=state, inline=True)
    embed.add_field(name="RAM", value=f"{ram} MB", inline=True)
    embed.add_field(name="CPU", value=f"{cpu}%", inline=True)

    await ctx.send(embed=embed)


@bot.command()
async def start(ctx):
    if not is_owner(ctx):
        return await ctx.send("❌ Not allowed muhehehehehe 🥀")

    r = requests.post(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/power",
        headers=HEADERS,
        json={"signal": "start"},
        timeout=10
    )

    if r.status_code in [200, 204]:
        await ctx.send("🟢 Server starting!")
    else:
        await ctx.send(f"❌ Error: {r.status_code}")


@bot.command()
async def stop(ctx):
    if not is_owner(ctx):
        return await ctx.send("❌ Not allowed muheheheh 🥀")

    r = requests.post(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/power",
        headers=HEADERS,
        json={"signal": "stop"},
        timeout=10
    )

    if r.status_code in [200, 204]:
        await ctx.send("🔴 Server stopping!")
    else:
        await ctx.send(f"❌ Error: {r.status_code}")


@bot.command()
async def restart(ctx):
    if not is_owner(ctx):
        return await ctx.send("❌ Not allowed")

    r = requests.post(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/power",
        headers=HEADERS,
        json={"signal": "restart"},
        timeout=10
    )

    if r.status_code in [200, 204]:
        await ctx.send("🔄 Server restarting!")
    else:
        await ctx.send(f"❌ Error: {r.status_code}")


@bot.command()
async def cmd(ctx, *, command):
    if not is_owner(ctx):
        return await ctx.send("❌ Not allowed")

    r = requests.post(
        f"{PANEL_URL}/api/client/servers/{SERVER_ID}/command",
        headers=HEADERS,
        json={"command": command},
        timeout=10
    )

    if r.status_code in [200, 204]:
        await ctx.send(f"✅ Executed: `{command}`")
    else:
        await ctx.send(f"❌ Error: {r.status_code}")

keep_alive()
bot.run(TOKEN)
