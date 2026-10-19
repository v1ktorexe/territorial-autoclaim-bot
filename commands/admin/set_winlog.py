import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone

from utils.permissions import check_bot_manager

class SetWinlog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="set_winlog", description="Set win log channel")
    @app_commands.describe(
        channel="Channel to send win logs",
        clan_name="Clan name to filter (required, case insensitive)"
    )
    async def set_winlog(self, interaction: discord.Interaction, channel: discord.TextChannel, clan_name: str):
        try:
            # Permission check - special user, server owner, or bot manager
            is_special_user = interaction.user.id == 780678948949721119
            is_server_owner = interaction.guild and interaction.user.id == interaction.guild.owner_id
            is_bot_manager = await check_bot_manager(self.bot, interaction)
            if not (is_special_user or is_server_owner or is_bot_manager):
                await interaction.response.send_message(
                    "❌ Only the server owner, bot manager, or authorized user can use this command!",
                    ephemeral=True
                )
                return
            
            if self.bot.db is None:
                await interaction.response.send_message("❌ Database not available!", ephemeral=True)
                return
            
            if not interaction.guild:
                await interaction.response.send_message("❌ This command can only be used in servers!", ephemeral=True)
                return
            
            guild_id = interaction.guild.id
            
            # Save/update winlog settings
            settings_data = {
                "guild_id": guild_id,
                "guild_name": interaction.guild.name,
                "channel_id": channel.id,
                "channel_name": channel.name,
                "set_by": interaction.user.id,
                "set_by_name": str(interaction.user),
                "timestamp": datetime.now(timezone.utc),
                "active": True
            }
            
            settings_data["clan_name"] = clan_name.strip()
            
            # Delete existing settings first to ensure clean replacement
            await self.bot.db.winlog_settings.delete_many({"guild_id": guild_id})
            
            # Insert new settings
            await self.bot.db.winlog_settings.insert_one(settings_data)
            
            # Create embed
            embed = discord.Embed(
                title="✅ Win Log Channel Set",
                description=f"Win logs will be monitored in {channel.mention}",
                color=0x00ff00
            )
            embed.add_field(name="Set by", value=interaction.user.mention, inline=True)
            embed.add_field(name="Server", value=interaction.guild.name, inline=True)
            
            embed.add_field(name="Clan Filter", value=f"Only **{clan_name}** wins", inline=True)
            
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            try:
                await interaction.response.send_message("❌ An error occurred while setting win log channel!", ephemeral=True)
            except:
                await interaction.followup.send("❌ An error occurred while setting win log channel!", ephemeral=True)
            print(f"Error in set_winlog command: {e}")

async def setup(bot):
    await bot.add_cog(SetWinlog(bot))