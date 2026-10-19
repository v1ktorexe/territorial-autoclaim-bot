import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone

class TestWinlog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="test_winlog", description="Test winlog scraping and configuration")
    async def test_winlog(self, interaction: discord.Interaction):
        try:
            # Permission check
            if interaction.user.id != 780678948949721119:
                if not interaction.guild.owner or interaction.user.id != interaction.guild.owner.id:
                    await interaction.response.send_message("❌ Only server owner or authorized user can use this command!", ephemeral=True)
                    return
            
            await interaction.response.defer(ephemeral=True)
            
            if self.bot.db is None:
                await interaction.followup.send("❌ Database not available!", ephemeral=True)
                return
            
            # Check winlog settings
            settings = await self.bot.db.winlog_settings.find_one({"guild_id": interaction.guild.id, "active": True})
            
            if not settings:
                await interaction.followup.send("❌ No active winlog settings found for this server!\nUse `/set_winlog` to configure.", ephemeral=True)
                return
            
            # Check account links
            links_count = await self.bot.db.account_links.count_documents({"guild_id": interaction.guild.id})
            
            # Test scraping
            embed = discord.Embed(
                title="🔍 Winlog System Diagnostic",
                color=0x00ff00
            )
            
            embed.add_field(
                name="✅ Configuration",
                value=f"Channel: <#{settings['channel_id']}>\\nClan Filter: `{settings.get('clan_name', 'ALL')}`",
                inline=False
            )
            
            embed.add_field(
                name="📊 Account Links",
                value=f"{links_count} accounts linked in this server",
                inline=False
            )
            
            embed.add_field(
                name="🔄 Monitoring Status",
                value=f"Active: {'✅ Yes' if self.bot.winlog_monitor and not self.bot.winlog_monitor.done() else '❌ No'}\\nProcessed Logs: {len(self.bot.processed_log_ids)}",
                inline=False
            )
            
            # Try to fetch from territorial.io
            try:
                import aiohttp
                timeout = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get('https://territorial.io/clan-results', headers={'User-Agent': 'Mozilla/5.0'}) as response:
                        if response.status == 200:
                            content = await response.text()
                            embed.add_field(
                                name="🌐 Website Connection",
                                value=f"✅ Successfully fetched {len(content)} characters from territorial.io",
                                inline=False
                            )
                        else:
                            embed.add_field(
                                name="🌐 Website Connection",
                                value=f"⚠️ HTTP {response.status} from territorial.io",
                                inline=False
                            )
            except Exception as e:
                embed.add_field(
                    name="🌐 Website Connection",
                    value=f"❌ Error: {str(e)}",
                    inline=False
                )
            
            # Bot permissions check
            channel = interaction.guild.get_channel(settings['channel_id'])
            if channel:
                perms = channel.permissions_for(interaction.guild.me)
                missing_perms = []
                if not perms.send_messages:
                    missing_perms.append("Send Messages")
                if not perms.embed_links:
                    missing_perms.append("Embed Links")
                if not perms.view_channel:
                    missing_perms.append("View Channel")
                
                if missing_perms:
                    embed.add_field(
                        name="⚠️ Missing Permissions",
                        value=f"Bot is missing: {', '.join(missing_perms)}",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="✅ Bot Permissions",
                        value="All required permissions granted",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="❌ Channel Error",
                    value="Configured channel not found!",
                    inline=False
                )
            
            embed.set_footer(text="If issues persist, check bot.log for detailed errors")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            try:
                await interaction.followup.send(f"❌ Error running diagnostic: {str(e)}", ephemeral=True)
            except:
                pass
            print(f"Error in test_winlog: {e}")

async def setup(bot):
    await bot.add_cog(TestWinlog(bot))
