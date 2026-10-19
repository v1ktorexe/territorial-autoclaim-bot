import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import logging

from utils.permissions import check_bot_manager

logger = logging.getLogger('TerritorialBot')

class AccountLinking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.SPECIAL_USER_ID = 780678948949721119

    def create_error_embed(self, title: str, description: str) -> discord.Embed:
        return discord.Embed(title=f"❌ {title}", description=description, color=discord.Color.red())
    
    @app_commands.command(name="account_linking", description="Securely link a Territorial.io account to a Discord user")
    @app_commands.describe(
        account_name="Territorial.io account name (max 5 characters)",
        user="Discord user to link"
    )
    async def account_linking(self, interaction: discord.Interaction, account_name: str, user: discord.Member):
        try:
            # 1. Permission checks
            if not interaction.guild:
                await interaction.response.send_message(
                    embed=self.create_error_embed("Server Only", "This command can only be used within a server environment."), 
                    ephemeral=True
                )
                return

            is_special_user = (interaction.user.id == self.SPECIAL_USER_ID)
            is_server_owner = (interaction.user.id == interaction.guild.owner_id)
            is_bot_manager = await check_bot_manager(self.bot, interaction)

            if not (is_special_user or is_server_owner or is_bot_manager):
                logger.warning(f"Unauthorized access attempt to /account_linking by User ID: {interaction.user.id} in Guild: {interaction.guild.id}")
                await interaction.response.send_message(
                    embed=self.create_error_embed("Permission Denied", "Restricted to the **Server Owner**, **Bot Manager**, or authorized **Special Access** personnel."), 
                    ephemeral=True
                )
                return

            # 2. Database readiness check
            if self.bot.db is None:
                logger.error("Database connection unavailable during /account_linking.")
                await interaction.response.send_message(
                    embed=self.create_error_embed("Database Error", "The database is currently unreachable. Please try again later or contact support."), 
                    ephemeral=True
                )
                return

            # 3. Validation of input semantics
            if user.bot:
                await interaction.response.send_message(
                    embed=self.create_error_embed("Invalid Target", "Cannot link a Territorial.io account to a Discord bot."), 
                    ephemeral=True
                )
                return

            account_name_stripped = account_name.strip()
            if not account_name_stripped:
                await interaction.response.send_message(
                    embed=self.create_error_embed("Invalid Input", "The account name cannot be empty or solely whitespace."), 
                    ephemeral=True
                )
                return
                
            if len(account_name_stripped) > 5:
                await interaction.response.send_message(
                    embed=self.create_error_embed("Invalid Length", f"Account name `{account_name_stripped}` exceeds the 5-character limit (Length: {len(account_name_stripped)})."), 
                    ephemeral=True
                )
                return

            normalized_account_name = account_name_stripped.lower()

            # 4. Global account uniqueness check (within the guild)
            try:
                existing_global_link = await self.bot.db.account_links.find_one({
                    "guild_id": interaction.guild.id,
                    "account_name": normalized_account_name
                })
                
                if existing_global_link:
                    linked_user_id = existing_global_link.get('user_id')
                    await interaction.response.send_message(
                        embed=self.create_error_embed(
                            "Account Already Claimed", 
                            f"The account `{account_name_stripped}` is already actively linked to <@{linked_user_id}> in this server."
                        ), 
                        ephemeral=True
                    )
                    return
            except Exception as db_err:
                logger.error(f"MongoDB read error during global uniqueness check: {db_err}")
                await interaction.response.send_message(
                    embed=self.create_error_embed("Database Read Error", "Failed to verify account uniqueness. Please report this issue."), 
                    ephemeral=True
                )
                return

            # 5. Rate limit / constraint checks for the target user
            try:
                current_link_count = await self.bot.db.account_links.count_documents({
                    "user_id": user.id,
                    "guild_id": interaction.guild.id
                })

                if current_link_count >= 3:
                    await interaction.response.send_message(
                        embed=self.create_error_embed(
                            "Limit Exceeded", 
                            f"{user.mention} has already reached the maximum limit of **3** linked accounts in this server."
                        ), 
                        ephemeral=True
                    )
                    return
            except Exception as db_err:
                logger.error(f"MongoDB read error during link counting: {db_err}")
                await interaction.response.send_message(
                    embed=self.create_error_embed("Database Read Error", "Failed to retrieve the current link count for the user."), 
                    ephemeral=True
                )
                return

            # 6. Database Insertion
            try:
                await self.bot.db.account_links.insert_one({
                    "user_id": user.id,
                    "guild_id": interaction.guild.id,
                    "account_name": normalized_account_name,
                    "account_name_original": account_name_stripped,
                    "linked_by": interaction.user.id,
                    "timestamp": datetime.now(timezone.utc)
                })
            except Exception as db_err:
                logger.error(f"MongoDB write error during account linking: {db_err}")
                await interaction.response.send_message(
                    embed=self.create_error_embed("Database Write Error", "Failed to save the new account link. Please try again."), 
                    ephemeral=True
                )
                return

            # 7. Notification handling
            dm_sent = True
            try:
                dm_embed = discord.Embed(
                    title="🔗 Account Successfully Linked",
                    description=f"Your Territorial.io account **`{account_name_stripped}`** has been officially linked to you by a Server Administrator!",
                    color=discord.Color.green()
                )
                dm_embed.add_field(name="Server", value=interaction.guild.name, inline=False)
                dm_embed.set_footer(text="If you trigger a win, points will be credited automatically.")
                await user.send(embed=dm_embed)
            except discord.Forbidden:
                dm_sent = False
                logger.info(f"Could not DMs linking notification to {user.id}. DMs closed.")
            except Exception as e:
                dm_sent = False
                logger.error(f"Unexpected error when sending DM to {user.id}: {e}")

            # 8. Success Response
            success_desc = f"Successfully registered **`{account_name_stripped}`** to {user.mention}."
            if not dm_sent:
                success_desc += "\n*(Note: Could not send DM to user, likely due to privacy settings.)*"

            success_embed = discord.Embed(
                title="✅ Account Link Registered",
                description=success_desc,
                color=discord.Color.brand_green()
            )
            success_embed.add_field(name="Slots Used", value=f"{current_link_count + 1}/3", inline=True)
            success_embed.set_footer(text=f"Normalized Key: {normalized_account_name}")
            
            await interaction.response.send_message(embed=success_embed, ephemeral=False)

        except Exception as e:
            logger.critical(f"Unhandled critical exception in /account_linking: {e}", exc_info=True)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        embed=self.create_error_embed("Critical Error", "An unexpected system error occurred."), 
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        embed=self.create_error_embed("Critical Error", "An unexpected system error occurred."), 
                        ephemeral=True
                    )
            except:
                pass

async def setup(bot):
    await bot.add_cog(AccountLinking(bot))