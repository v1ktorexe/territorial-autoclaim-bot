import discord
from discord.ext import commands
from discord import app_commands

class ForceRefreshRewards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="force_refresh_rewards", description="Force refresh reward roles for eligible users")
    async def force_refresh_rewards(self, interaction: discord.Interaction):
        try:
            # Check if user is authorized (special user, admin, or bot manager)
            is_special_user = interaction.user.id == 780678948949721119
            is_admin = interaction.user.guild_permissions.administrator
            is_bot_manager = discord.utils.get(interaction.user.roles, name="Bot Manager") is not None
            
            if not (is_special_user or is_admin or is_bot_manager):
                await interaction.response.send_message("❌ You need Administrator permission or Bot Manager role!", ephemeral=True)
                return
            
            await interaction.response.defer()
            
            if self.bot.db is None:
                await interaction.followup.send("❌ Database not available!")
                return
            
            guild_id = interaction.guild.id
            processed_count = 0
            
            # Get all reward roles for this guild
            rewards = await self.bot.db.reward_roles.find({
                "guild_id": guild_id,
                "active": True
            }).sort("amount", -1).to_list(None)
            
            if not rewards:
                await interaction.followup.send("❌ No reward roles configured!")
                return
            
            # Group by type
            points_rewards = [r for r in rewards if r["type"] == "points"]
            wins_rewards = [r for r in rewards if r["type"] == "wins"]
            
            # Process points rewards
            for reward_type, type_rewards in [("points", points_rewards), ("wins", wins_rewards)]:
                if not type_rewards:
                    continue
                
                collection = self.bot.db.points if reward_type == "points" else self.bot.db.wins
                
                # Get all users with totals
                pipeline = [
                    {"$match": {"guild_id": guild_id}},
                    {"$group": {"_id": "$user_id", "total": {"$sum": "$amount"}}}
                ]
                users = await collection.aggregate(pipeline).to_list(None)
                
                for user_data in users:
                    user_id = user_data["_id"]
                    total = user_data["total"]
                    
                    # Find ALL eligible rewards
                    eligible_rewards = []
                    for reward in type_rewards:
                        if total >= reward["amount"]:
                            eligible_rewards.append(reward)
                    
                    if not eligible_rewards:
                        continue
                    
                    # Check if user is in guild
                    member = interaction.guild.get_member(user_id)
                    if not member:
                        continue
                    
                    # Give all eligible roles
                    for reward in eligible_rewards:
                        role = interaction.guild.get_role(reward["role_id"])
                        if not role:
                            continue
                        
                        # Skip if already has role
                        if role in member.roles:
                            continue
                        
                        try:
                            await member.add_roles(role, reason=f"Reward refresh: {total:,} {reward_type}")
                            processed_count += 1
                        except:
                            continue
            
            embed = discord.Embed(
                title="✅ Reward Refresh Complete",
                description=f"Processed {processed_count} role assignments",
                color=0x00ff00
            )
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            try:
                await interaction.followup.send(f"❌ Error: {str(e)[:100]}")
            except:
                pass

async def setup(bot):
    await bot.add_cog(ForceRefreshRewards(bot))