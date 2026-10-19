import discord
from discord.ext import commands
import asyncio
import logging
import os
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from aiohttp import web
import aiohttp
import re
from datetime import datetime, timezone, timedelta

# new fixes 
# Load environment variables
load_dotenv()

# Source channel where the terristats bot posts win logs
SOURCE_WINLOG_CHANNEL_ID = 1460318338432631041

# Configure logging with separate levels for file and console
# File: DEBUG level (all detailed logs)
# Console: WARNING level (only important messages for Railway)
logger = logging.getLogger('TerritorialBot')
logger.setLevel(logging.DEBUG)

# File handler - DEBUG level (all logs saved to file)
file_handler = logging.FileHandler('bot.log', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(name)s | %(message)s'))

# Console handler - INFO level (better visibility for deployment)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Suppress other noisy loggers
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('aiohttp').setLevel(logging.WARNING)

class WinLogClaimView(discord.ui.View):
    def __init__(self, bot, points, message_id, guild_id, header_text, auto_credited_text, auto_credited_users=None):
        super().__init__(timeout=300)  # 5 minutes
        self.bot = bot
        self.points = points
        self.message_id = message_id
        self.guild_id = guild_id
        self.claimed_users = {}  # Store user_id: multiplier
        self.auto_credited_users = set(auto_credited_users or [])  # Users who got auto-credited
        self.header_text = header_text
        self.auto_credited_text = auto_credited_text
        self.message = None  # Store message reference
        self.creation_time = datetime.now(timezone.utc)
    
    @discord.ui.button(label="Claim 1x", style=discord.ButtonStyle.secondary, emoji="🎯")
    async def claim_1x(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.claim_points(interaction, 1.0)
    
    @discord.ui.button(label="Crown 1.2x", style=discord.ButtonStyle.primary, emoji="👑")
    async def claim_12x(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.claim_points(interaction, 1.2)
    
    @discord.ui.button(label="Solo 1.7x", style=discord.ButtonStyle.success, emoji="⭐")
    async def claim_17x(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.claim_points(interaction, 1.7)
    
    async def claim_points(self, interaction: discord.Interaction, multiplier: float):
        try:
            user_id = interaction.user.id
            
            # Check if 5 minutes have passed
            if datetime.now(timezone.utc) - self.creation_time > timedelta(minutes=5):
                try:
                    await interaction.response.send_message("❌ This win log has expired!", ephemeral=True)
                except (discord.InteractionResponded, discord.NotFound):
                    pass
                return
            
            # Check if user was auto-credited
            if user_id in self.auto_credited_users:
                try:
                    await interaction.response.send_message("❌ You were already auto-credited for this win!", ephemeral=True)
                except (discord.InteractionResponded, discord.NotFound):
                    pass
                return
            
            # Check if user already claimed
            if user_id in self.claimed_users:
                try:
                    await interaction.response.send_message("❌ You already claimed points from this log!", ephemeral=True)
                except (discord.InteractionResponded, discord.NotFound):
                    pass
                return
            
            # Add to claimed users with multiplier
            self.claimed_users[user_id] = multiplier
            
            # Add points to user account with multiplier
            final_points = self.points * multiplier
            success = await self.bot.add_winlog_points(user_id, self.guild_id, final_points)
            
            if success:
                try:
                    # Get server multiplier for display
                    server_multiplier_data = await self.bot.db.multipliers.find_one({"guild_id": self.guild_id, "active": True})
                    server_multiplier = server_multiplier_data["multiplier"] if server_multiplier_data else 1.0
                    display_points = final_points * server_multiplier
                    
                    # Update message with claimed users
                    try:
                        claimed_mentions = []
                        for uid, mult in self.claimed_users.items():
                            if mult == 1.0:
                                claimed_mentions.append(f"<@{uid}>")
                            else:
                                claimed_mentions.append(f"<@{uid}> `{mult}x`")
                        
                        new_content = self.header_text
                        if claimed_mentions:
                            new_content += "\n\nClaimed by:\n" + "\n".join(claimed_mentions)
                        if self.auto_credited_text:
                            new_content += self.auto_credited_text
                            
                        await interaction.message.edit(content=new_content, embed=None, view=self)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                        logger.error(f"Failed to update claim embed: {e}")
                    
                    # Send success message
                    embed = discord.Embed(
                        title="✅ Points Claimed!",
                        description=f"You received **{display_points:,.1f} points** and **1 win**!",
                        color=0x00ff00
                    )
                    
                    if multiplier > 1.0:
                        embed.description += f"\n*Base: {self.points} x {multiplier} = {final_points} points*"
                    if server_multiplier > 1.0:
                        embed.description += f"\n*Server multiplier: {server_multiplier}x*"
                    
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    
                except Exception as e:
                    try:
                        await interaction.response.send_message("✅ Points added successfully!", ephemeral=True)
                    except:
                        pass
            else:
                self.claimed_users.pop(user_id, None)  # Remove from claimed if failed
                try:
                    await interaction.response.send_message("❌ Failed to add points!", ephemeral=True)
                except:
                    pass
                
        except Exception as e:
            self.claimed_users.pop(user_id, None)  # Remove from claimed if error
            try:
                await interaction.response.send_message("❌ An error occurred!", ephemeral=True)
            except:
                pass
            logger.error(f"Error in winlog claim: {e}")
    
    async def on_timeout(self):
        try:
            if self.message:
                # Just remove buttons, don't edit message content
                await self.message.edit(view=None)
        except Exception as e:
            logger.error(f"Error in winlog timeout: {e}")

class TerritorialBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None
        )
        
        self.mongodb_client = None
        self.db = None
        self.join_channel_id = 1391460533982462023
        self.leave_channel_id = 1391460563401576659
        self.log_channel_id = 1391463375304921108
        self.reward_monitor = None
        self.processed_rewards = set()
        self.last_log_time = 0
        self.winlog_claims = {}
        self.processed_winlogs = set()
        
    async def setup_hook(self):
        """Called when the bot is starting up"""
        logger.info("Setting up bot...")
        
        # Connect to MongoDB
        await self.connect_mongodb()
        
        # Load all commands from subfolders
        await self.load_commands()
        
        # Sync slash commands
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands")
            
            # Force sync for current guild if in development
            if len(self.guilds) == 1:
                guild = self.guilds[0]
                synced_guild = await self.tree.sync(guild=guild)
                logger.info(f"Force synced {len(synced_guild)} commands for guild {guild.name}")
                
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
    
    async def connect_mongodb(self):
        """Connect to MongoDB"""
        try:
            mongodb_uri = os.getenv('MONGODB_URI')
            if not mongodb_uri:
                logger.error("MONGODB_URI not found in environment variables")
                return
                
            self.mongodb_client = AsyncIOMotorClient(mongodb_uri)
            self.db = self.mongodb_client.autoclaim_bot
            
            # Test connection
            await self.mongodb_client.admin.command('ping')
            logger.info("Successfully connected to MongoDB")
            
            # Create unique index on processed_winlogs to prevent duplicates
            try:
                await self.db.processed_winlogs.create_index(
                    [("clan_name", 1), ("log_id", 1)],
                    unique=True
                )
                logger.info("Ensured unique index on processed_winlogs")
            except Exception as index_err:
                logger.warning(f"Index creation on processed_winlogs: {index_err}")
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
    
    async def load_commands(self):
        """Load all commands from commands folder and subfolders"""
        commands_path = Path("commands")
        
        if not commands_path.exists():
            logger.warning("Commands folder not found, creating it...")
            commands_path.mkdir(exist_ok=True)
            return
        
        loaded_count = 0
        failed_count = 0
        total_files = sum(1 for f in commands_path.rglob("*.py") if not f.name.startswith("__"))
        
        # Walk through all Python files in commands folder and subfolders
        for py_file in commands_path.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
                
            # Convert path to module format
            module_path = str(py_file.with_suffix("")).replace(os.sep, ".")
            cmd_name = py_file.stem
            
            try:
                await self.load_extension(module_path)
                loaded_count += 1
                print(f"/{cmd_name} - loaded")
            except Exception as e:
                failed_count += 1
                logger.error(f"Failed to load /{cmd_name}: {e}")
        
        print(f"\nTotal commands: {loaded_count}/{total_files} loaded")
    
    async def migrate_account_links(self):
        """Auto-migrate account links to lowercase for consistent matching"""
        try:
            if self.db is None:
                logger.warning("Cannot migrate account links - database not available")
                return
            
            # Get all account links that need migration
            all_links = await self.db.account_links.find({}).to_list(None)
            migrated_count = 0
            
            for link in all_links:
                current_name = link.get("account_name", "")
                normalized_name = current_name.lower().strip()
                
                # Only update if not already normalized
                if current_name != normalized_name or not link.get("account_name_original"):
                    await self.db.account_links.update_one(
                        {"_id": link["_id"]},
                        {
                            "$set": {
                                "account_name": normalized_name,
                                "account_name_original": link.get("account_name_original", current_name)
                            }
                        }
                    )
                    migrated_count += 1
            
            if migrated_count > 0:
                logger.info(f"Migrated {migrated_count} account links to lowercase")
            else:
                logger.info("All account links already normalized")
                
        except Exception as e:
            logger.error(f"Error migrating account links: {e}")
    
    async def on_ready(self):
        """Called when bot is ready"""
        logger.warning(f"DIAGNOSTIC: Bot logged in as {self.user} (ID: {self.user.id})")
        logger.warning(f"DIAGNOSTIC: Connected to {len(self.guilds)} guilds")
        
        # List guilds for debugging
        for guild in self.guilds:
            logger.warning(f"DIAGNOSTIC: Serving Guild: {guild.name} (ID: {guild.id})")

        # Auto-migrate account links to lowercase on startup
        await self.migrate_account_links()
        
        # Set bot status explicitly to Online
        activity = discord.CustomActivity(name='Made by v1ktor')
        await self.change_presence(status=discord.Status.online, activity=activity)
        logger.warning("DIAGNOSTIC: Bot status set to ONLINE and 'Made by v1ktor'")
        
        # Send startup log
        log_channel = self.get_channel(self.log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="🟢 Bot Started",
                color=0x00ff00,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(name="Status", value="Online", inline=True)
            embed.add_field(name="Guilds", value=str(len(self.guilds)), inline=True)
            embed.add_field(name="Commands", value=str(len(self.tree.get_commands())), inline=True)
            await log_channel.send(embed=embed)
        
        # Start reward monitoring
        self.start_reward_monitoring()
        
        # Start war monitoring
        self.start_war_monitoring()
        

    
    async def on_guild_join(self, guild):
        """Called when bot joins a guild"""
        try:
            # Get the user who added the bot
            async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=1):
                if entry.target.id == self.user.id:
                    inviter = entry.user
                    break
            else:
                inviter = None
            
            # Send notification to join channel
            channel = self.get_channel(self.join_channel_id)
            if channel:
                embed = discord.Embed(
                    title="🎉 Bot Added to New Server",
                    color=0x00ff00,
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="Server", value=f"{guild.name} (ID: {guild.id})", inline=False)
                embed.add_field(name="Members", value=str(guild.member_count), inline=True)
                embed.add_field(name="Owner", value=str(guild.owner), inline=True)
                
                if inviter:
                    embed.add_field(name="Added by", value=f"{inviter} (ID: {inviter.id})", inline=False)
                
                embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
                
                await channel.send(embed=embed)
            
            # Log to database
            if self.db:
                await self.db.guild_events.insert_one({
                    "event": "join",
                    "guild_id": guild.id,
                    "guild_name": guild.name,
                    "member_count": guild.member_count,
                    "inviter_id": inviter.id if inviter else None,
                    "inviter_name": str(inviter) if inviter else None,
                    "timestamp": datetime.now(timezone.utc)
                })
            
            logger.info(f"Joined guild: {guild.name} (ID: {guild.id}) - Added by: {inviter}")
            
        except Exception as e:
            logger.error(f"Error handling guild join: {e}")
    
    async def on_guild_remove(self, guild):
        """Called when bot leaves a guild"""
        try:
            # Send notification to leave channel
            channel = self.get_channel(self.leave_channel_id)
            if channel:
                embed = discord.Embed(
                    title="❌ Bot Removed from Server",
                    color=0xff0000,
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="Server", value=f"{guild.name} (ID: {guild.id})", inline=False)
                embed.add_field(name="Members", value=str(guild.member_count), inline=True)
                embed.add_field(name="Owner", value=str(guild.owner), inline=True)
                
                embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
                
                await channel.send(embed=embed)
            
            # Log to database
            if self.db:
                await self.db.guild_events.insert_one({
                    "event": "leave",
                    "guild_id": guild.id,
                    "guild_name": guild.name,
                    "member_count": guild.member_count,
                    "timestamp": datetime.now(timezone.utc)
                })
            
            logger.info(f"Left guild: {guild.name} (ID: {guild.id})")
            
        except Exception as e:
            logger.error(f"Error handling guild leave: {e}")
    
    async def on_command_error(self, ctx, error):
        """Global error handler"""
        logger.error(f"Command error in {ctx.command}: {error}")
    
    async def on_message(self, message):
        """Handle incoming messages: process commands and watch for win logs."""
        if message.author == self.user:
            return
        
        # Watch the terristats source channel for win log messages
        if message.channel.id == SOURCE_WINLOG_CHANNEL_ID:
            await self._handle_winlog_message(message)
        
        # Required for commands to function
        await self.process_commands(message)
    
    def _parse_winlog_message(self, content: str):
        """Parse a win log message from the source channel (code block format)."""
        try:
            if '```' not in content:
                return None
            parts = content.split('```')
            if len(parts) < 2:
                return None
            log_text = parts[1].strip()
            if not log_text:
                return None

            current_log = {}
            for line in log_text.split('\n'):
                line = line.strip()
                if ':' in line:
                    key, value = line.split(':', 1)
                    current_log[key.strip()] = value.strip()

            if not current_log:
                return None

            winning_clan_raw = current_log.get('Winning Clan', '')
            if '[' in winning_clan_raw and ']' in winning_clan_raw:
                clan_name = winning_clan_raw[winning_clan_raw.index('[')+1:winning_clan_raw.index(']')]
            else:
                clan_name = winning_clan_raw

            map_name = current_log.get('Map', 'Unknown')
            try:
                player_count = int(current_log.get('Player Count', 0))
            except ValueError:
                player_count = 0
            contest = current_log.get('Contest', 'No')
            prev_points = current_log.get('Prev. Points', '0')
            curr_points = current_log.get('Curr. Points', '0')
            clan_winners_str = current_log.get('Clan Winners', '')
            log_time = current_log.get('Time', '')

            is_contest = contest.lower() == 'yes'
            claimable_points = player_count * 2 if is_contest else player_count

            return {
                'clan_name': clan_name,
                'map_name': map_name,
                'player_count': player_count,
                'base_points': player_count,
                'claimable_points': claimable_points,
                'is_contest': is_contest,
                'prev_points': prev_points,
                'curr_points': curr_points,
                'clan_winners_accounts': [a.strip() for a in clan_winners_str.split(',') if a.strip()],
                'log_time': log_time
            }
        except Exception as e:
            logger.debug(f'Failed to parse winlog message: {e}')
            return None
    
    async def _handle_winlog_message(self, message):
        """Process a win log message received from the source channel."""
        try:
            content = message.content.strip()
            if not content or '```' not in content:
                return

            parsed = self._parse_winlog_message(content)
            if not parsed:
                logger.debug(f'Could not parse winlog message: {content[:100]}')
                return

            logger.warning(f"NEW WIN from source channel: [{parsed['clan_name']}] {parsed['map_name']} {parsed['claimable_points']}pts")

            await self.process_winlog_for_guilds(
                parsed['clan_name'],
                parsed['base_points'],
                parsed['claimable_points'],
                parsed['map_name'],
                parsed['is_contest'],
                parsed['player_count'],
                parsed['prev_points'],
                parsed['curr_points'],
                parsed.get('log_time') or datetime.now(timezone.utc).isoformat(),
                parsed.get('clan_winners_accounts', [])
            )
        except Exception as e:
            logger.error(f'Error handling winlog message: {e}')
    
    def start_reward_monitoring(self):
        """Start reward monitoring task"""
        if self.reward_monitor is None or self.reward_monitor.done():
            self.reward_monitor = asyncio.create_task(self.monitor_rewards())
            logger.info("Started reward monitoring")
    
    async def monitor_rewards(self):
        """Monitor rewards every 3 seconds"""
        import time
        while True:
            try:
                if self.db is None:
                    await asyncio.sleep(3)
                    continue
                
                current_time = time.time()
                
                # Get all active reward settings sorted by amount (highest first)
                rewards = await self.db.reward_roles.find({"active": True}).sort("amount", -1).to_list(None)
                
                # Log every 5 seconds to Discord
                if current_time - self.last_log_time >= 5:
                    log_channel = self.get_channel(1392150722698809374)
                    if log_channel and len(rewards) > 0:
                        embed = discord.Embed(
                            description=f"Reward monitor: {len(self.processed_rewards)} processed, {len(rewards)} active settings",
                            color=0x2b2d31
                        )
                        await log_channel.send(embed=embed)
                    self.last_log_time = current_time
                
                # Group rewards by guild and type to find highest eligible roles
                guild_rewards = {}
                for reward in rewards:
                    guild_id = reward["guild_id"]
                    reward_type = reward["type"]
                    if guild_id not in guild_rewards:
                        guild_rewards[guild_id] = {"points": [], "wins": []}
                    guild_rewards[guild_id][reward_type].append(reward)
                
                for guild_id, types in guild_rewards.items():
                    guild = self.get_guild(guild_id)
                    if not guild:
                        continue
                    
                    for reward_type, type_rewards in types.items():
                        if not type_rewards:
                            continue
                        
                        # Get all users' totals for this type in this guild
                        collection = self.db.points if reward_type == "points" else self.db.wins
                        pipeline = [
                            {"$match": {"guild_id": guild_id}},
                            {"$group": {"_id": "$user_id", "total": {"$sum": "$amount"}}}
                        ]
                        users = await collection.aggregate(pipeline).to_list(None)
                        
                        for user_data in users:
                            user_id = user_data["_id"]
                            total = user_data["total"]
                            
                            # Find highest eligible reward for this user
                            highest_reward = None
                            for reward in type_rewards:
                                if total >= reward["amount"]:
                                    if not highest_reward or reward["amount"] > highest_reward["amount"]:
                                        highest_reward = reward
                            
                            if not highest_reward:
                                continue
                            
                            reward = highest_reward
                            channel = guild.get_channel(reward["channel_id"])
                            role = guild.get_role(reward["role_id"])
                            
                            if not channel or not role:
                                continue
                            
                            # Check role hierarchy
                            if role >= guild.me.top_role:
                                continue
                            
                            # Check if user is actually in THIS guild
                            member = guild.get_member(user_id)
                            if not member:
                                try:
                                    member = await guild.fetch_member(user_id)
                                except:
                                    continue
                            
                            # Create unique key
                            key = f"{reward['_id']}_{user_id}"
                            
                            # Race condition fix: check and add atomically
                            if key in self.processed_rewards:
                                continue
                            self.processed_rewards.add(key)
                            
                            # Check if user already has this exact role
                            if role in member.roles:
                                continue
                            
                            # Give milestone role (keep all previous roles)
                            try:
                                # Give new role
                                await member.add_roles(role, reason=f"Milestone: {total:,.0f} {reward['type']}")
                                
                                # Send milestone notification
                                embed = discord.Embed(
                                    description=f"Congratulations {member.mention}, you have reached {total:,.0f} {reward['type']} and you are rewarded with {role.mention}",
                                    color=0x00ff00
                                )
                                await channel.send(embed=embed)
                                
                                # Send log to specific channel
                                log_channel = self.get_channel(1392150722698809374)
                                if log_channel:
                                    log_embed = discord.Embed(
                                        description=f"Gave {role.name} to {member.display_name} for {total:,} {reward['type']} in {guild.name}",
                                        color=0x00ff00
                                    )
                                    await log_channel.send(embed=log_embed)
                                
                            except discord.Forbidden:
                                self.processed_rewards.discard(key)
                                log_channel = self.get_channel(1392150722698809374)
                                if log_channel:
                                    embed = discord.Embed(
                                        description=f"No permission to give {role.name} to {member.display_name} in {guild.name}",
                                        color=0xff0000
                                    )
                                    await log_channel.send(embed=embed)
                    
                            except Exception as e:
                                self.processed_rewards.discard(key)
                                log_channel = self.get_channel(1392150722698809374)
                                if log_channel:
                                    embed = discord.Embed(
                                        description=f"Failed to give {role.name} to {member.display_name}: {str(e)}",
                                        color=0xff0000
                                    )
                                    await log_channel.send(embed=embed)
                
                await asyncio.sleep(3)
                
            except Exception as e:
                logger.error(f"Error in reward monitoring: {e}")
                await asyncio.sleep(3)
    
    async def trigger_reward_check(self, user_id, guild_id):
        """Trigger immediate reward check for a user"""
        # Reward system runs automatically every 3 seconds
        pass
        
    async def process_winlog_for_guilds(self, winning_clan, base_points, claimable_points, map_name, is_contest, player_count, prev_points, curr_points, win_time, clan_winners_accounts):
        """Process win log for all configured guilds"""
        try:
            if self.db is None:
                return
            
            # Get all active winlog settings
            settings = await self.db.winlog_settings.find({"active": True}).to_list(None)
            
            for setting in settings:
                guild = self.get_guild(setting["guild_id"])
                if not guild:
                    logger.debug(f"Guild {setting['guild_id']} not found, skipping")
                    continue
                
                clan_filter = setting.get("clan_name", "").strip()
                
                # Robust match: strip all non-alphanumeric characters
                cf_clean = re.sub(r'[^a-zA-Z0-9]', '', clan_filter.lower())
                wc_clean = re.sub(r'[^a-zA-Z0-9]', '', winning_clan.lower())
                
                clan_matches = (cf_clean == wc_clean)
                
                if not clan_matches:
                    logger.info(f"Skipped {guild.name}: filter '{clan_filter}' didn't match '{winning_clan}'")
                    continue
                
                logger.info(f"Processing win for guild {guild.name} (Matched: {winning_clan})")
                
                channel = guild.get_channel(setting["channel_id"])
                if not channel:
                    logger.warning(f"CRITICAL: Winlog channel {setting['channel_id']} not found in guild {guild.name}!")
                    continue
                
                # Auto-credit linked accounts
                auto_credited = []
                
                # Get all account links for this guild
                all_guild_links = await self.db.account_links.find({"guild_id": guild.id}).to_list(None)
                logger.debug(f"Found {len(all_guild_links)} account links in guild")
                
                # Process each win account
                for win_account in clan_winners_accounts:
                    # Normalize win account for matching
                    win_account_normalized = win_account.lower().strip()
                    
                    # Try EXACT match first (both should be lowercase now)
                    linked_user = await self.db.account_links.find_one({
                        "account_name": win_account_normalized,
                        "guild_id": guild.id
                    })
                    
                    # Try case-insensitive regex match if exact match fails
                    if not linked_user:
                        linked_user = await self.db.account_links.find_one({
                            "account_name": {"$regex": f"^{re.escape(win_account_normalized)}$", "$options": "i"},
                            "guild_id": guild.id
                        })
                    
                    # Fallback: iterate through all guild links
                    if not linked_user:
                        for link in all_guild_links:
                            link_account = link.get("account_name", "").lower().strip()
                            if link_account == win_account_normalized:
                                linked_user = link
                                break
                    
                    # Try partial match as last resort
                    if not linked_user:
                        for link in all_guild_links:
                            link_account = link.get("account_name", "").lower().strip()
                            if win_account_normalized in link_account or link_account in win_account_normalized:
                                linked_user = link
                                break
                    
                    if linked_user:
                        user_id = linked_user["user_id"]
                        stored_account = linked_user.get("account_name", "unknown")
                        original_account = linked_user.get("account_name_original", stored_account)
                        logger.debug(f"Found linked user: {win_account_normalized} -> User ID: {user_id}")
                        
                        # Verify user exists in guild
                        user = guild.get_member(user_id)
                        if not user:
                            try:
                                user = await guild.fetch_member(user_id)
                            except Exception as fetch_err:
                                logger.debug(f"Could not fetch user {user_id}: {fetch_err}")
                                continue
                        
                        # Add points
                        success = await self.add_winlog_points(user_id, guild.id, claimable_points)
                        
                        if success:
                            auto_credited.append(f"<@{user_id}>")
                            # Log successful auto-credit (visible in console)
                            logger.warning(f"Auto-credited {user.display_name} ({original_account}) with {claimable_points} points")
                            
                            # Send DM notification
                            try:
                                multiplier_data = await self.db.multipliers.find_one({"guild_id": guild.id, "active": True})
                                server_multiplier = multiplier_data["multiplier"] if multiplier_data else 1.0
                                final_points = claimable_points * server_multiplier
                                
                                embed = discord.Embed(
                                    title="🎉 Auto-Credited Points!",
                                    description=f"You received **{final_points:,.1f} points** and **1 win** from [{winning_clan}] win on {map_name}!\n\nAccount: `{original_account}`",
                                    color=0x00ff00
                                )
                                if is_contest:
                                    embed.description += f"\n*Contest game: {base_points} x 2 = {claimable_points} points!*"
                                
                                await user.send(embed=embed)
                            except Exception:
                                pass  # DM failed, no need to log
                        else:
                            logger.debug(f"Failed to add points to user {user_id}")
                    else:
                        logger.debug(f"No linked user for account '{win_account}'")
                
                logger.debug(f"Auto-credited {len(auto_credited)} users")
                
                # Create text content per exact format specifications
                if is_contest:
                    header_text = f"**{winning_clan}** {map_name} 2 x {base_points} [{prev_points} -> {curr_points}]"
                else:
                    header_text = f"**{winning_clan}** {map_name} {base_points} [{prev_points} -> {curr_points}]"
                
                auto_credited_text = ""
                if auto_credited:
                    auto_credited_text = "\n\nAuto-Credited:\n" + "\n".join(auto_credited)
                
                # Extract user IDs from auto_credited list & prepare mentions
                auto_credited_user_ids = []
                for mention in auto_credited:
                    match = re.search(r'<@(\d+)>', mention)
                    if match:
                        auto_credited_user_ids.append(int(match.group(1)))
                
                # Initial message content
                initial_content = header_text + auto_credited_text
                
                # Create claim button view with auto-credited users
                view = WinLogClaimView(self, claimable_points, hash(win_time), guild.id, header_text, auto_credited_text, auto_credited_user_ids)
                
                logger.info(f"Attempting to send win log to {guild.name} #{channel.name}")
                
                try:
                    sent_message = await channel.send(content=initial_content, embed=None, view=view)
                    view.message = sent_message
                    logger.info(f"SUCCESS: Win log sent, message ID: {sent_message.id}")
                except (discord.Forbidden, discord.HTTPException) as e:
                    logger.warning(f"FAILED to send winlog to {guild.name} #{channel.name}: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing winlog for guilds: {e}")
    
    def start_war_monitoring(self):
        """Start war monitoring task"""
        if not hasattr(self, 'war_monitor') or self.war_monitor is None or self.war_monitor.done():
            self.war_monitor = asyncio.create_task(self.monitor_wars())
            logger.info("Started war monitoring")
    
    async def monitor_wars(self):
        """Monitor active wars and end them when time expires"""
        while True:
            try:
                if self.db is None:
                    await asyncio.sleep(10)
                    continue
                
                # Get all active wars
                active_wars = await self.db.cult_wars.find({"active": True}).to_list(None)
                
                for war in active_wars:
                    end_time = war["end_time"]
                    # Ensure end_time is timezone-aware UTC
                    if end_time.tzinfo is None:
                        end_time = end_time.replace(tzinfo=timezone.utc)
                    elif end_time.tzinfo != timezone.utc:
                        end_time = end_time.astimezone(timezone.utc)
                    if datetime.now(timezone.utc) >= end_time:
                        await self.end_war_automatically(war)
                
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                logger.error(f"Error in war monitoring: {e}")
                await asyncio.sleep(10)
    
    async def end_war_automatically(self, war):
        """Automatically end a war and determine winner"""
        try:
            guild = self.get_guild(war["guild_id"])
            if not guild:
                return
            
            # Get cult data
            from bson import ObjectId
            attacker_cult = await self.db.cults.find_one({"_id": ObjectId(war["attacker_cult_id"])})
            defender_cult = await self.db.cults.find_one({"_id": ObjectId(war["defender_cult_id"])})
            
            if not attacker_cult or not defender_cult:
                return
            
            # Calculate scores
            attacker_score = await self.calculate_war_score(attacker_cult, war)
            defender_score = await self.calculate_war_score(defender_cult, war)
            
            # Determine winner
            if attacker_score > defender_score:
                winner_cult = attacker_cult
                loser_cult = defender_cult
                winner_score = attacker_score
                loser_score = defender_score
            elif defender_score > attacker_score:
                winner_cult = defender_cult
                loser_cult = attacker_cult
                winner_score = defender_score
                loser_score = attacker_score
            else:
                winner_cult = None  # Tie
                winner_score = attacker_score
                loser_score = defender_score
            
            # Update war record
            await self.db.cult_wars.update_one(
                {"_id": war["_id"]},
                {
                    "$set": {
                        "active": False,
                        "ended_at": datetime.now(timezone.utc),
                        "attacker_score": attacker_score,
                        "defender_score": defender_score,
                        "winner_cult_id": str(winner_cult["_id"]) if winner_cult else None,
                        "auto_ended": True
                    }
                }
            )
            
            # Create result embed
            if winner_cult:
                embed = discord.Embed(
                    title="🏆 WAR ENDED - VICTORY!",
                    description=f"{winner_cult['cult_icon']} **{winner_cult['cult_name']}** has won the war against {loser_cult['cult_icon']} **{loser_cult['cult_name']}**!",
                    color=0x00ff00
                )
                embed.add_field(name="Final Scores", value=f"{winner_cult['cult_name']}: {winner_score:,.0f}\n{loser_cult['cult_name']}: {loser_score:,.0f}", inline=True)
            else:
                embed = discord.Embed(
                    title="🤝 WAR ENDED - TIE!",
                    description=f"The war between {attacker_cult['cult_icon']} **{attacker_cult['cult_name']}** and {defender_cult['cult_icon']} **{defender_cult['cult_name']}** ended in a tie!",
                    color=0xffa500
                )
                embed.add_field(name="Final Scores", value=f"{attacker_cult['cult_name']}: {attacker_score:,.0f}\n{defender_cult['cult_name']}: {defender_score:,.0f}", inline=True)
            
            embed.add_field(name="War Type", value=war["race_type"].title(), inline=True)
            
            # Get all members to notify
            all_members = set(attacker_cult["members"] + defender_cult["members"])
            ping_mentions = " ".join([f"<@{user_id}>" for user_id in all_members])
            
            # Find a channel to send the message (try to find the original channel or use first available)
            channel = None
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break
            
            if channel:
                await channel.send(f"{ping_mentions}\n", embed=embed)
            
            # Send DMs to all members
            for user_id in all_members:
                try:
                    user = guild.get_member(user_id)
                    if user:
                        await user.send(embed=embed)
                except:
                    pass
            
        except Exception as e:
            logger.error(f"Error ending war automatically: {e}")
    
    async def calculate_war_score(self, cult, war):
        """Calculate cult's score for the war period"""
        total_score = 0
        
        for member_id in cult["members"]:
            if war["race_type"] in ["points", "both"]:
                points_result = await self.db.points.aggregate([
                    {"$match": {
                        "user_id": member_id,
                        "guild_id": war["guild_id"],
                        "cult_id": str(cult["_id"]),
                        "timestamp": {"$gte": war["start_time"], "$lte": war["end_time"]}
                    }},
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                ]).to_list(1)
                total_score += points_result[0]["total"] if points_result else 0
            
            if war["race_type"] in ["wins", "both"]:
                wins_result = await self.db.wins.aggregate([
                    {"$match": {
                        "user_id": member_id,
                        "guild_id": war["guild_id"],
                        "cult_id": str(cult["_id"]),
                        "timestamp": {"$gte": war["start_time"], "$lte": war["end_time"]}
                    }},
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
                ]).to_list(1)
                total_score += wins_result[0]["total"] if wins_result else 0
        
        return total_score
    
    async def add_winlog_points(self, user_id, guild_id, points):
        """Add points from winlog claim with extensive validation"""
        try:
            if self.db is None:
                logger.error("Database is None")
                return False
            guild = self.get_guild(guild_id)
            if not guild:
                logger.error(f"Guild {guild_id} not found")
                return False
            user = guild.get_member(user_id)
            if not user:
                try:
                    user = await guild.fetch_member(user_id)
                except Exception as fetch_error:
                    logger.error(f"Failed to fetch user {user_id}: {fetch_error}")
                    return False
            # Get multiplier setting
            multiplier = 1.0
            try:
                multiplier_data = await self.db.multipliers.find_one({"guild_id": guild_id, "active": True})
                if multiplier_data and "multiplier" in multiplier_data:
                    multiplier = float(multiplier_data["multiplier"])
            except Exception:
                pass
            final_points = points * multiplier
            # Get user's cult
            user_cult_data = None
            try:
                user_cult_data = await self.db.cults.find_one({
                    "guild_id": guild_id,
                    "members": user_id,
                    "active": True
                })
            except Exception:
                pass
            timestamp = datetime.now(timezone.utc)
            points_data = {
                "user_id": user_id,
                "user_name": str(user),
                "guild_id": guild_id,
                "guild_name": guild.name,
                "amount": final_points,
                "base_amount": points,
                "multiplier_used": multiplier,
                "cult_id": str(user_cult_data["_id"]) if user_cult_data else None,
                "cult_name": user_cult_data["cult_name"] if user_cult_data else None,
                "type": "winlog_auto",
                "timestamp": timestamp
            }
            wins_data = {
                "user_id": user_id,
                "user_name": str(user),
                "guild_id": guild_id,
                "guild_name": guild.name,
                "amount": 1,
                "cult_id": str(user_cult_data["_id"]) if user_cult_data else None,
                "cult_name": user_cult_data["cult_name"] if user_cult_data else None,
                "type": "winlog_auto",
                "timestamp": timestamp
            }
            try:
                points_result = await self.db.points.insert_one(points_data)
            except Exception as points_error:
                logger.error(f"Failed to save points transaction: {points_error}")
                return False
            try:
                wins_result = await self.db.wins.insert_one(wins_data)
            except Exception as wins_error:
                logger.error(f"Failed to save wins transaction: {wins_error}")
                try:
                    await self.db.points.delete_one({"_id": points_result.inserted_id})
                except:
                    pass
                return False
            return True
        except Exception as e:
            logger.error(f"Error adding winlog points to user {user_id}: {e}")
            return False
    
    async def close(self):
        """Clean shutdown"""
        try:
            if self.reward_monitor:
                self.reward_monitor.cancel()
                logger.info("Reward monitoring stopped")
        except Exception as e:
            logger.error(f"Error stopping reward monitoring: {e}")
        
        try:
            if hasattr(self, 'war_monitor') and self.war_monitor:
                self.war_monitor.cancel()
                logger.info("War monitoring stopped")
        except Exception as e:
            logger.error(f"Error stopping war monitoring: {e}")
        
        try:
            if self.mongodb_client:
                self.mongodb_client.close()
                logger.info("MongoDB connection closed")
        except Exception as e:
            logger.error(f"Error closing MongoDB: {e}")
        
        await super().close()

# Health check server for Railway
async def health_check(request):
    return web.Response(text="Bot is running", status=200)

async def start_health_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.warning(f"Health server started on port {port}")

async def main():
    """Main function to run the bot"""
    # Start health check server FIRST for Railway
    await start_health_server()
    
    bot = TerritorialBot()
    
    try:
        discord_token = os.getenv('DISCORD_TOKEN')
        if not discord_token:
            logger.error("DISCORD_TOKEN not found in environment variables")
            return
        
        await bot.start(discord_token)
        
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested")
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())





    # Send Messages - All commands
    # Use Slash Commands - Command system
    # Embed Links - All embeds
    # Read Message History - Context awareness
    # View Channels - Access channels
    # Manage Roles - Reward role system
    # Attach Files - Profile graphs/charts
    # Add Reactions - Interactive features
    
