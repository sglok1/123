import discord
from discord.ext import commands
import os
import json
from dotenv import load_dotenv
import re
from datetime import datetime, timedelta
from collections import defaultdict

# ==================== CONFIGURATION ====================
load_dotenv()

# Load or initialize config
CONFIG_FILE = "config.json"
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

config = load_config()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is not set in .env!")

owner_id = config.get("owner_id")
log_channel_id = config.get("log_channel_id")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

whitelisted = {owner_id} if owner_id else set()
LINK_PATTERN = re.compile(r"(https?://\S+|www\.\S+)")
ban_count = defaultdict(int)
TIMEOUT_DURATION = 600

# ==================== HELPER FUNCTIONS ====================
async def send_dm(user, message):
    try:
        await user.send(f"\U0001F514 **Server Security Alert** \U0001F514\n{message}")
    except discord.Forbidden:
        print(f"Could not DM {user}")

async def log_action(guild, message):
    if not log_channel_id:
        return
    log_channel = guild.get_channel(log_channel_id)
    if log_channel:
        embed = discord.Embed(
            description=message,
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        await log_channel.send(embed=embed)

# ==================== DECORATORS ====================
def is_owner():
    async def predicate(ctx):
        return ctx.author.id == config.get("owner_id")
    return commands.check(predicate)

# ==================== BOT EVENTS ====================
@bot.event
async def on_ready():
    print(f'\u2705 Logged in as {bot.user} (ID: {bot.user.id})')
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="for rule breakers"
    ))

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if LINK_PATTERN.search(message.content) and message.author.id not in whitelisted:
        await message.delete()
        await send_dm(message.author, "\U0001F6AB **Links are not allowed in this server!**")
        await log_action(message.guild, 
            f"\u274C {message.author.mention} tried to send a link in {message.channel.mention}.\n"
            f"**Message:** {message.content[:100]}..."
        )

    if any(mention in message.content.lower() for mention in ["@everyone", "@here"]) and message.author.id not in whitelisted:
        await message.delete()
        await message.author.timeout(
            timedelta(seconds=TIMEOUT_DURATION),
            reason="Mass mention violation"
        )
        await send_dm(message.author, 
            f"\u23F3 **You have been timed out for {TIMEOUT_DURATION//60} minutes**\n"
            "Reason: Mentioning @everyone or @here without permission"
        )
        await log_action(message.guild,
            f"\u26A0\uFE0F {message.author.mention} was timed out for mass mentions in {message.channel.mention}"
        )

    await bot.process_commands(message)

# ==================== SECURITY EVENTS ====================
@bot.event
async def on_guild_channel_create(channel):
    async for entry in channel.guild.audit_logs(action=discord.AuditLogAction.channel_create, limit=1):
        if entry.user.id not in whitelisted:
            await channel.delete()
            await entry.user.ban(reason="Unauthorized channel creation")
            await log_action(channel.guild,
                f"\U0001F6A8 {entry.user.mention} was banned for creating channel #{channel.name}"
            )

@bot.event
async def on_guild_role_create(role):
    async for entry in role.guild.audit_logs(action=discord.AuditLogAction.role_create, limit=1):
        if entry.user.id not in whitelisted:
            await role.delete()
            await entry.user.ban(reason="Unauthorized role creation")
            await log_action(role.guild,
                f"\U0001F6A8 {entry.user.mention} was banned for creating role @{role.name}"
            )

@bot.event
async def on_member_ban(guild, user):
    async for entry in guild.audit_logs(action=discord.AuditLogAction.ban, limit=1):
        if entry.user.id not in whitelisted:
            await guild.ban(entry.user, reason="Unauthorized ban attempt")
            await log_action(guild,
                f"\U0001F6A8 {entry.user.mention} was banned for trying to ban {user.mention}"
            )

# ==================== MODERATION COMMANDS ====================
@bot.command()
@is_owner()
async def whitelist(ctx, member: discord.Member):
    whitelisted.add(member.id)
    await ctx.send(f"\u2705 {member.mention} has been whitelisted.")
    await log_action(ctx.guild, f"\U0001F4DD {ctx.author.mention} whitelisted {member.mention}")

@bot.command()
@is_owner()
async def unwhitelist(ctx, member: discord.Member):
    whitelisted.discard(member.id)
    await ctx.send(f"\u274C {member.mention} has been removed from whitelist.")
    await log_action(ctx.guild, f"\U0001F4DD {ctx.author.mention} unwhitelisted {member.mention}")

@bot.command()
async def setowner(ctx, member: discord.Member = None):
    global owner_id, config, whitelisted
    if config.get("owner_id") is not None:
        await ctx.send("\u274C Owner is already set. Restart bot to reset.")
        return

    target = member or ctx.author
    owner_id = target.id
    config["owner_id"] = owner_id
    whitelisted = {owner_id}
    save_config(config)

    await ctx.send(f"\U0001F451 {target.mention} is now the bot owner.")
    await log_action(ctx.guild, f"\U0001F451 Owner set to {target.mention} by {ctx.author.mention}")

@bot.command()
async def setlogchannel(ctx, channel: discord.TextChannel):
    global log_channel_id, config
    if ctx.author.id != config.get("owner_id"):
        await ctx.send("\u26D4 Only the bot owner can set the log channel.")
        return

    log_channel_id = channel.id
    config["log_channel_id"] = log_channel_id
    save_config(config)

    await ctx.send(f"\U0001F4D3 Logging channel set to {channel.mention}.")
    await log_action(ctx.guild, f"\U0001F4D3 Log channel set to {channel.mention} by {ctx.author.mention}")

# ==================== ERROR HANDLING ====================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("\u274C Missing required arguments!")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("\u26D4 You don't have permission to use this command.")
    else:
        await log_action(ctx.guild, f"\u26A0\uFE0F Error in command `{ctx.command}`: {str(error)}")
        raise error

# ==================== RUN BOT ====================
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        print("Invalid Discord token. Please check your .env file.")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
