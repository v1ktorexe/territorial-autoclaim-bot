import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone

class Add(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="add", description="Add points to your account")
    @app_commands.describe(points="Points to add (1-1500)")
    async def add(self, interaction: discord.Interaction, points: float):
        try:
            await interaction.response.defer()
            # Validation
            if points < 1 or points > 1500:
                await interaction.followup.send("❌ Points must be between 1 and 1500!", ephemeral=True)
                return
            
            if self.bot.db is None:
                await interaction.followup.send("❌ Database not available!", ephemeral=True)
                return
            
            if not interaction.guild:
                await interaction.followup.send("❌ This command can only be used in servers!", ephemeral=True)
                return
            
            user_id = interaction.user.id
            guild_id = interaction.guild.id
            
            # Get multiplier setting
            multiplier_data = await self.bot.db.multipliers.find_one({"guild_id": guild_id, "active": True})
            multiplier = multiplier_data["multiplier"] if multiplier_data else 1.0
            
            # Calculate final points
            final_points = points * multiplier
            
            # Save points transaction
            try:
                await self.bot.db.points.insert_one({
                    "_id": f"points_{interaction.id}",
                    "user_id": user_id,
                    "user_name": str(interaction.user),
                    "guild_id": guild_id,
                    "guild_name": interaction.guild.name,
                    "amount": final_points,
                    "base_amount": points,
                    "multiplier_used": multiplier,
                    "type": "add",
                    "timestamp": datetime.now(timezone.utc)
                })
            except Exception as e:
                # If a duplicate execution hits, safely exit
                if 'duplicate key error' in str(e).lower() or 'e11000' in str(e).lower():
                    return
                raise e
            
            # Save win transaction
            try:
                await self.bot.db.wins.insert_one({
                    "_id": f"wins_{interaction.id}",
                    "user_id": user_id,
                    "user_name": str(interaction.user),
                    "guild_id": guild_id,
                    "guild_name": interaction.guild.name,
                    "amount": 1,
                    "type": "add",
                    "timestamp": datetime.now(timezone.utc)
                })
            except Exception as e:
                if 'duplicate key error' in str(e).lower() or 'e11000' in str(e).lower():
                    pass
                else:
                    raise e
            
            # Get server-specific totals
            points_pipeline = [
                {"$match": {"user_id": user_id, "guild_id": guild_id}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]
            
            wins_pipeline = [
                {"$match": {"user_id": user_id, "guild_id": guild_id}},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
            ]
            
            total_points_result = await self.bot.db.points.aggregate(points_pipeline).to_list(1)
            total_wins_result = await self.bot.db.wins.aggregate(wins_pipeline).to_list(1)
            
            user_points = total_points_result[0]["total"] if total_points_result else final_points
            user_wins = total_wins_result[0]["total"] if total_wins_result else 1
            
            # Create embed
            embed = discord.Embed(color=0x00ff00)
            embed.set_author(
                name=interaction.guild.name,
                icon_url=interaction.guild.icon.url if interaction.guild.icon else None
            )
            
            # Build description dynamically
            embed.description = (
                f"{final_points:,.1f} points added to your balance\n"
                f"**New Points:** {user_points:,.1f}\n"
                f"**New Wins:** {user_wins:,}"
            )
            
            if multiplier > 1:
                embed.description += f"\n**Multiplier:** {multiplier}x"
            
            if multiplier > 1:
                embed.description += f"\n*({points:,.1f} x {multiplier} = {final_points:,.1f})*"
            
            await interaction.followup.send(embed=embed)
            
            # Trigger reward check
            await self.bot.trigger_reward_check(user_id, guild_id)
            
        except Exception as e:
            try:
                await interaction.followup.send("❌ An error occurred while processing your request!", ephemeral=True)
            except:
                pass
            print(f"Error in add command: {e}")
            import traceback
            traceback.print_exc()

async def setup(bot):
    await bot.add_cog(Add(bot))