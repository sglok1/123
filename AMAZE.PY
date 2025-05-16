import discord
import random
import asyncio
from discord.ext import commands
from discord import ui, ButtonStyle
from dotenv import load_dotenv
import os

# Load environment variables from .env
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)
bot.verified_role_id = None
bot.verification_channel_id = None

class VerificationView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Verify", style=ButtonStyle.green, custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: ui.Button):
        num1 = random.randint(1, 10)
        num2 = random.randint(1, 10)
        puzzle = f"{num1} + {num2}"
        answer = num1 + num2

        await interaction.response.send_message(
            f"Solve this to verify: **{puzzle}** = ?\n(Reply with your answer below)",
            ephemeral=False
        )

        def check(m):
            return (m.author == interaction.user and
                    m.channel == interaction.channel and
                    not m.is_system())

        try:
            msg = await bot.wait_for('message', check=check, timeout=60)
            if msg.content.strip() == str(answer):
                await msg.delete()
                await self.grant_access(interaction)
            else:
                await msg.delete()
                response = await interaction.followup.send(
                    f"❌ Wrong answer! Try again.", ephemeral=False)
                await asyncio.sleep(3)
                await response.delete()
                await interaction.delete_original_response()

        except asyncio.TimeoutError:
            response = await interaction.followup.send(
                "⏰ Verification timed out.", ephemeral=False)
            await asyncio.sleep(3)
            await response.delete()
            await interaction.delete_original_response()

    async def grant_access(self, interaction: discord.Interaction):
        if not bot.verified_role_id:
            await self.cleanup_error(interaction, "Verification role not configured")
            return

        role = interaction.guild.get_role(bot.verified_role_id)
        if not role:
            await self.cleanup_error(interaction, "Verification role not found")
            return

        try:
            await interaction.user.add_roles(role)
            await interaction.delete_original_response()

            for channel in interaction.guild.channels:
                if channel.id != bot.verification_channel_id:
                    await channel.set_permissions(interaction.user, view_channel=True)

            success_msg = await interaction.channel.send(
                f"{interaction.user.mention} ✅ Verification successful!",
                delete_after=5
            )

        except Exception as e:
            await self.cleanup_error(interaction, "Failed to assign role")
            print(f"Error: {e}")

    async def cleanup_error(self, interaction: discord.Interaction, error_msg: str):
        response = await interaction.followup.send(f"⚠️ {error_msg}", ephemeral=False)
        await asyncio.sleep(3)
        await response.delete()
        await interaction.delete_original_response()

@bot.event
async def on_member_join(member):
    if not all([bot.verified_role_id, bot.verification_channel_id]):
        return

    verification_channel = member.guild.get_channel(bot.verification_channel_id)
    if not verification_channel:
        return

    await verification_channel.set_permissions(
        member,
        view_channel=True,
        send_messages=True,
        read_message_history=True
    )

    for channel in member.guild.channels:
        if channel.id != bot.verification_channel_id:
            await channel.set_permissions(member, view_channel=False)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    bot.add_view(VerificationView())

@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx, verification_channel_id: int, verified_role_id: int):
    """Setup the verification system using raw IDs"""
    guild = ctx.guild

    verification_channel = guild.get_channel(verification_channel_id)
    verified_role = guild.get_role(verified_role_id)

    if not verification_channel or not verified_role:
        await ctx.send("❌ Invalid channel ID or role ID.")
        return

    bot.verified_role_id = verified_role.id
    bot.verification_channel_id = verification_channel.id

    await verification_channel.set_permissions(
        guild.default_role,
        view_channel=True,
        send_messages=True,
        read_message_history=True
    )

    embed = discord.Embed(
        title="🔐 SERVER VERIFICATION",
        description="Click the button below to verify and access the server!",
        color=discord.Color.blurple()
    )

    await verification_channel.purge()
    await verification_channel.send(embed=embed, view=VerificationView())
    await ctx.send("✅ Verification system setup complete!", delete_after=5)
    await ctx.message.delete()

# Run the bot using the token from the environment
bot.run(TOKEN)
