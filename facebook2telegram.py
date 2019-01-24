#!/usr/bin/python3
# coding=utf-8

# For loading configuration
import ast
import configparser

# For tracking pages' last update time
import json

# File Handling
from os import remove
from os import path

# For exiting the program
import sys
from time import sleep

# Date comparison
from datetime import datetime

# Download media
from urllib import request
import requests

# telegram-bot-python and Errors
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram.error import TelegramError
from telegram.error import InvalidToken
from telegram.error import BadRequest
from telegram.error import TimedOut
from telegram.error import NetworkError

# facebook-sdk
import facebook

# Logging
import logging
import logging.handlers
logging.basicConfig(
	format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
	level = logging.INFO,
	handlers = [
		logging.handlers.TimedRotatingFileHandler(
			filename = 'log/fb2tg.log',
			when = 'midnight',
			atTime = datetime( year=2018, month=1, day=1, hour=0, minute=0, second=0 ).time() ) ] )
logger = logging.getLogger(__name__)

# youtube-dl. Removal is pending
import youtube_dl
ydl = youtube_dl.YoutubeDL({'outtmpl': '%(id)s%(ext)s'})


# ======================================================== #

# ----- General Global Variables ----- #
configurations = {}
working_directory = None
last_update_record_file = None
last_update_records = {}
show_usage_limit_status = False

# ----- Facebook Global Variables ----- #
facebook_graph = None
facebook_pages = None
facebook_job = None
facebook_pages_request_index = 0

# ----- Telegram Global Variables ----- #
telegram_bot = None
telegram_updater = None
telegram_dispatcher = None
telegram_job_queue = None

# ----- Other Variables ----- #
# Check if the first message is posted to telegram
# If posted, and encounter error afterward, the update is considered as posted
# Otherwise, it may be the error from telegram, and should be posted again.
headerPosted = False

# -------------------------------------------------------- #


def loadConfiguration( filename ):
	"""
	Loads the configurations from the .ini file
	and stores them in global variables.
	Use example.botsettings.ini as an example.
	"""

	global configurations

	config = configparser.SafeConfigParser()
	config.read(filename)

	try:
		# Load facebook configurations
		configurations['locale'] = config.get( 'facebook', 'locale' )
		configurations['facebook_token'] = config.get( 'facebook', 'token' )
		configurations['facebook_pages'] = ast.literal_eval( config.get('facebook', 'pages' ) )
		configurations['facebook_refresh_rate'] = 1900.0
		configurations['facebook_refresh_rate_default'] = float( config.get( 'facebook', 'refreshrate' ) )
		configurations['facebook_page_per_request'] = int( config.get( 'facebook', 'pageperrequest' ) )
		configurations['allow_status'] = config.getboolean( 'facebook', 'status' )
		configurations['allow_photo'] = config.getboolean( 'facebook', 'photo' )
		configurations['allow_video'] = config.getboolean( 'facebook', 'video' )
		configurations['allow_link'] = config.getboolean( 'facebook', 'link' )
		configurations['allow_shared'] = config.getboolean( 'facebook', 'shared' )
		configurations['allow_message'] = config.getboolean( 'facebook', 'message' )

		# Load telegram configurations
		configurations['telegram_token'] = config.get( 'telegram', 'token' )
		configurations['channel_id'] = config.get( 'telegram', 'channel' )

	except configparser.NoSectionError:
		logger.error( 'Fatal Error: Missing or invalid configurations file.' )
		sys.exit( 'Fatal Error: Missing or invalid configurations file.' )
	except configparser.NoOptionError:
		logger.error( 'Fatal Error: Missing or invalid option in configurations file.' )
		sys.exit( 'Fatal Error: Missing or invalid option in configurations file.' )
	except ValueError:
		logger.error( 'Fatal Error: Missing or invalid value in configurations file.' )
		sys.exit( 'Fatal Error: Missing or invalid value in configurations file.' )
	except SyntaxError:
		logger.error( 'Fatal Error: Syntax error in page list.' )
		sys.exit( 'Fatal Error: Syntax error in page list.' )


	logger.info( 'Loaded configurations:' )
	logger.info( 'Locale: ' + str( configurations['locale'] ) )
	logger.info( 'Channel: ' + configurations['channel_id'] )
	logger.info( 'Refresh rate: ' + str( configurations['facebook_refresh_rate'] ) )
	logger.info( 'Allow Status: ' + str( configurations['allow_status'] ) )
	logger.info( 'Allow Photo: ' + str( configurations['allow_photo'] ) )
	logger.info( 'Allow Video: ' + str( configurations['allow_video'] ) )
	logger.info( 'Allow Link: ' + str( configurations['allow_link'] ) )
	logger.info( 'Allow Shared: ' + str( configurations['allow_shared'] ) )
	logger.info( 'Allow Message: ' + str( configurations['allow_message'] ) )


def loadFacebookGraph( facebook_token ):
	"""
	Initialize Facebook GraphAPI with the token loaded from the configurations file
	"""
	global facebook_graph
	facebook_graph = facebook.GraphAPI( access_token = facebook_token, version = '3.0', timeout = 120 )


def loadTelegramBot( telegram_token ):
	"""
	Initialize Telegram Bot API with the token loaded from the configurations file
	"""
	global telegram_bot
	global telegram_updater
	global telegram_dispatcher
	global telegram_job_queue

	try:
		telegram_bot = telegram.Bot(token=telegram_token)
	except InvalidToken:
		logger.error( 'Fatal Error: Invalid Telegram Token' )
		sys.exit( 'Fatal Error: Invalid Telegram Token' )

	telegram_updater = Updater(token=telegram_token)
	telegram_dispatcher = telegram_updater.dispatcher
	telegram_job_queue = telegram_updater.job_queue


def parsePostCreatedTime( post ):
	"""
	Get the post's created time from the given post's object.
	"""
	date_format = "%Y-%m-%dT%H:%M:%S+0000"
	post_date = datetime.strptime( post['created_time'], date_format )
	return post_date


class JSONDatetimeEncoder( json.JSONEncoder ):
	"""
	Converts the 'datetime' type to an ISO timestamp for the JSON dumper
	"""
	def default( self, o ):
		if isinstance( o, datetime ):
			return o.isoformat()

		return super( JSONDatetimeEncoder, self ).default( o )



def dateTimeDecoder( pairs, date_format="%Y-%m-%dT%H:%M:%S" ):
	"""
	Converts the ISO timestamp to 'datetime' type for the JSON loader
	"""
	d = {}

	for k, v in pairs:
		if isinstance(v, str):
			try:
				d[k] = datetime.strptime( v, date_format )
			except ValueError:
				d[k] = v
		else:
			d[k] = v

	return d


def loadLastUpdateRecordFromFile():
	"""
	Load and return the last update records from the given filename.
	"""
	with open( last_update_record_file, 'r' ) as f:
		loaded_json = json.load( f, object_pairs_hook = dateTimeDecoder )

	logger.info( 'Load last update records successfully.' )
	return loaded_json


def updateLastUpdateRecordToFile():
	"""
	Update the last update records to the specific file.
	"""
	with open( last_update_record_file, 'w' ) as f:
		json.dump( obj = last_update_records,
					fp = f,
					sort_keys = True,
					cls = JSONDatetimeEncoder,
					indent = '\t' )

	logger.info( 'Update last update records successfully.' )
	return True


def checkNewPagesExistness( facebook_pages ):
	"""
	Finds if the facebook_pages are in the last update record file.
	If the last update record file does not exists, the function
	creates an empty last update record file.
	If any page in facebook_pages is not in the last update record
	file, we fetch the last update time from facebook graph and store
	it in the last update record file.
	"""
	logger.info( 'Checking for new added pages.' )

	global last_update_records

	# Check if dates.json exists.  If not, create one.
	try:
		last_update_records = loadLastUpdateRecordFromFile()
	except (IOError, ValueError):
		last_update_records = {}
		updateLastUpdateRecordToFile()

	# Check if any new page ID is added
	new_facebook_pages = []

	for page in facebook_pages:
		if page not in last_update_records:
			new_facebook_pages.append( page )
			logger.info( 'Checking if page {} went online...'.format( page ) )

	if len( new_facebook_pages ) == 0:
		return

	# Request 40 pages' status only.
	startPage = 0
	while startPage < len( new_facebook_pages ):
		endPage = min( (startPage + 40), len( new_facebook_pages ) )

		# Fetch new added pages' last update time
		last_update_times = facebook_graph.get_objects(
				ids = new_facebook_pages[startPage:endPage],
				fields = 'name,posts.limit(1){created_time}'
		)

		for page in new_facebook_pages[startPage:endPage]:
			try:
				last_update_record = last_update_times[page]['posts']['data'][0]
				last_update_records[page] = parsePostCreatedTime( last_update_record )
				updateLastUpdateRecordToFile()
				logger.info( 'Page {} ({}) went online.'.format( last_update_times[page]['name'], page ) )

			except KeyError:
				logger.warning( 'Page {} not found.'.format( page ) )

		startPage += 40
		sleep(10)


def getDirectURLVideo(video_id):
	"""
	Get direct URL for the video using GraphAPI and the post's 'object_id'
	"""
	logger.info('Getting direct URL...')
	video_post = facebook_graph.get_object(
			id=video_id,
			fields='source')

	return video_post['source']


def getDirectURLVideoYDL(URL):
	"""
	Get direct URL for the video using youtube-dl
	"""
	try:
		with ydl:
			result = ydl.extract_info(URL, download=False) #Just get the link

		#Check if it's a playlist
		if 'entries' in result:
			video = result['entries'][0]
		else:
			video = result

		return video['url']
	except youtube_dl.utils.DownloadError:
		logger.info('youtube-dl failed to parse URL.')
		return None


def postPhotoToChat(post, post_message, bot, chat_id):
	"""
	Posts the post's picture with the appropriate caption.
	"""
	direct_link = post['full_picture']

	try:
		message = bot.send_photo(
			chat_id=chat_id,
			photo=direct_link,
			caption=post_message)
		return message

	except (BadRequest, TimedOut):
		"""
		If the picture can't be sent using its URL,
		it is downloaded locally and uploaded to Telegram.
		"""
		try:
			logger.info('Sending by URL failed, downloading file...')
			request.urlretrieve(direct_link, working_directory+'/temp.jpg')
			logger.info('Sending file...')
			with open(working_directory+'/temp.jpg', 'rb') as picture:
				message = bot.send_photo(
					chat_id=chat_id,
					photo=picture,
					caption=post_message,
					timeout=120)
			remove(working_directory+'/temp.jpg')   #Delete the temp picture
			return message

		except TimedOut:
			"""
			If there is a timeout, try again with a higher
			timeout value for 'bot.send_photo'
			"""
			logger.warning('File upload timed out, trying again...')
			logger.info('Sending file...')
			with open(working_directory+'/temp.jpg', 'rb') as picture:
				message = bot.send_photo(
					chat_id=chat_id,
					photo=picture,
					caption=post_message,
					timeout=200)
			remove(working_directory+'/temp.jpg')   #Delete the temp picture
			return message

		except BadRequest:
			logger.warning('Could not send photo file, sending link...')
			bot.send_message(    #Send direct link as a message
				chat_id=chat_id,
				text=direct_link+'\n'+post_message)
			return message


def postVideoToChat(post, post_message, bot, chat_id):
	"""
	This function tries to pass 3 different URLs to the Telegram API
	instead of downloading the video file locally to save bandwidth.

	*First option":  Direct video source
	*Second option": Direct video source from youtube-dl
	*Third option":  Direct video source with smaller resolution
	"Fourth option": Download file locally for upload
	"Fifth option":  Send the video link
	"""
	#If youtube link, post the link and short text if exists
	if 'caption' in post and post['caption'] == 'youtube.com':
		if post_message:
			logger.info( 'Send post message with Youtube Link' )
			bot.send_message( chat_id = chat_id, text = post_message )
		else:
			logger.info('Sending YouTube link...')
			bot.send_message(
				chat_id=chat_id,
				text=post['link'])
	else:
		if 'object_id' in post:
			direct_link = getDirectURLVideo(post['object_id'])

		try:
			message = bot.send_video(
				chat_id=chat_id,
				video=direct_link,
				caption=post_message)
			return message

		except TelegramError:        #If the API can't send the video
			try:
				logger.info('Could not post video, trying youtube-dl...')
				message = bot.send_video(
					chat_id=chat_id,
					video=getDirectURLVideoYDL(post['link']),
					caption=post_message)
				return message

			except TelegramError:
				try:
					logger.warning('Could not post video, trying smaller res...')
					message = bot.send_video(
						chat_id=chat_id,
						video=post['source'],
						caption=post_message)
					return message

				except TelegramError:    #If it still can't send the video
					try:
						logger.warning('Sending by URL failed, downloading file...')
						request.urlretrieve(post['source'],
											working_directory+'/temp.mp4')
						logger.info('Sending file...')
						with open(working_directory+'/temp.mp4', 'rb') as video:
							message = bot.send_video(
								chat_id=chat_id,
								video=video,
								caption=post_message,
								timeout=120)
						remove(working_directory+'/temp.mp4')   #Delete the temp video
						return message
					except NetworkError:
						logger.warning('Could not post video, sending link...')
						message = bot.send_message(#Send direct link as message
							chat_id=chat_id,
							text=direct_link+'\n'+post_message)
						return message


def postLinkToChat(post, post_message, bot, chat_id):
	"""
	Checks if the post has a message with its link in it. If it does,
	it sends only the message. If not, it sends the link followed by the
	post's message.
	"""
	if post['link'] in post_message:
		post_link = ''
	else:
		post_link = post['link']

	bot.send_message(
		chat_id=chat_id,
		text=post_link+'\n'+post_message)


def checkIfAllowedAndPost(post, bot, chat_id):
	"""
	Checks the type of the Facebook post and if it's allowed by the
	configurations file, then calls the appropriate function for each type.
	"""
	#If it's a shared post, call this function for the parent post
	if 'parent_id' in post and configurations['allow_shared']:
		logger.info('This is a shared post.')

		if 'message' in post:
			bot.send_message( chat_id = chat_id, text = post['message'] )


		parent_post = facebook_graph.get_object(
			id=post['parent_id'],
			fields='created_time,type,message,full_picture,story,\
					source,link,caption,parent_id,object_id',
			locale=configurations['locale'])
		logger.info('Accessing parent post...')
		checkIfAllowedAndPost(parent_post, bot, chat_id)
		return True

#	If there's a message in the post, and it's allowed by the
#	configurations file, store it in 'post_message', which will be passed to
#	another function based on the post type.
	if 'message' in post and configurations['allow_message']:
		post_message = post['message']
	else:
		post_message = ''

	#Telegram doesn't allow media captions with more than 200 characters
	#Send separate message with the post's message
	if (len(post_message) > 200) and \
						(post['type'] == 'photo' or post['type'] == 'video'):
		separate_message = post_message
		post_message = ''
		send_separate = True
	else:
		separate_message = ''
		send_separate = False

	if post['type'] == 'photo' and configurations['allow_photo']:
		logger.info('Posting photo...')
		media_message = postPhotoToChat(post, post_message, bot, chat_id)
		if send_separate:
			media_message.reply_text(separate_message)
		return True
	elif post['type'] == 'video' and configurations['allow_video']:
		logger.info('Posting video...')
		media_message = postVideoToChat(post, post_message, bot, chat_id)
		if send_separate:
			media_message.reply_text(separate_message)
		return True
	elif post['type'] == 'status' and configurations['allow_status']:
		logger.info('Posting status...')
		try:
			bot.send_message(
				chat_id=chat_id,
				text=post['message'])
			return True
		except KeyError:
			logger.warning('Message not found, posting story...')
			bot.send_message(
				chat_id=chat_id,
				text=post['story'])
			return True
	elif post['type'] == 'link' and configurations['allow_link']:
		logger.info('Posting link...')
		postLinkToChat(post, post_message, bot, chat_id)
		return True
	else:
		logger.warning('This post is a {}, skipping...'.format(post['type']))
		bot.send_message("The post's type is {}, skipping".format(post['type']))
		return False


def postToChat(post, bot, chat_id):
	"""
	Calls another function for posting and checks if it returns True.
	"""
	global headerPosted

	text = '{} updated a post at {}.\n'.format(post['pagename'].replace('_', '\_'), post['created_time']) + \
		   'ID: {}\n\n'.format(post['page']) + \
		   '>>> [Link to the Post]({}) <<<'.format(post['permalink_url'])
	bot.send_message(
		chat_id = chat_id,
		text = text,
		parse_mode='Markdown',
		disable_web_page_preview=True )
	sleep(3)
	headerPosted = True

	if checkIfAllowedAndPost(post, bot, chat_id):
		logger.info('Posted.')
	else:
		logger.warning('Failed.')


def postNewPosts(new_posts_total, chat_id):
	global last_update_records
	global headerPosted
	new_posts_total_count = len(new_posts_total)

	time_to_sleep = 30
	post_left = len(new_posts_total)

	logger.info('Posting {} new posts to Telegram...'.format(new_posts_total_count))
	for post in new_posts_total:
		posts_page = post['page']
		logger.info('Posting NEW post from page {}...'.format(posts_page))
		headerPosted = False

		try:
			postToChat(post, telegram_bot, chat_id)
		except BadRequest as e:
			logger.error('Error: Telegram could not send the message')
			logger.error('Message: {}'.format(e.message))
			telegram_bot.send_message( chat_id = chat_id, text = 'Bad Request Exception')
			#raise
		except KeyError:
			logger.error('Error: Got Key Error, ignore the post from {}'.format(post['pagename']))
			logger.exception(' ')
			telegram_bot.send_message( chat_id = chat_id, text = 'Key Error Exception from page {}'.format(post['pagename']))
			headerPosted = True
		except Exception as e:
			msg = 'Unknown Error: {} when processing page {}'.format( type(e), posts_page )
			logger.error(msg)
			telegram_bot.send_message( chat_id = chat_id, text = msg )
		finally:
			if headerPosted:
				last_update_records[posts_page] = parsePostCreatedTime(post)
				updateLastUpdateRecordToFile()
				post_left -= 1
			telegram_bot.send_message( chat_id = chat_id, text = '{} post(s) left'.format(post_left) )

		logger.info('Waiting {} seconds before next post...'.format(time_to_sleep))
		sleep(int(time_to_sleep))
	logger.info('{} posts posted to Telegram'.format(new_posts_total_count))


def filterNewPosts( fb_page_ids, page_data, last_update_records ):
	# Iterate each page in fb_page_ids and filtering the new posts

	new_posts_result = []
	for page_id in fb_page_ids:
		try:
			# Extract the latest posts of the page.
			# Facebook returns the newest 25 posts.
			posts = page_data[page_id]['posts']['data']
			new_posts = list(
					filter(
						lambda post: parsePostCreatedTime( post ) > last_update_records[page_id],
						posts
					) )

			if new_posts:
				logger.info( '{}({}) has {} new posts'.format(
						page_data[page_id]['name'], page_id, len( new_posts ) ) )

				# Add additional information of the post.
				for post in new_posts:
					post['page'] = page_id
					post['pagename'] = page_data[page_id]['name']

				new_posts_result = new_posts_result + new_posts

		except KeyError:
			# The page ID is not in the returning data
			logger.warning( 'Page not found: {}'.format( page_id ) )
			continue

	# Sort the new posts in chronological order
	new_posts_result.sort( key=parsePostCreatedTime )
	return new_posts_result



def updateFacebookPageListForRequest():
	"""
	Rotate the facebook pages for the next request.
	"""
	global facebook_pages_request_index
	global facebook_pages

	facebook_page_list = configurations['facebook_pages']

	facebook_pages_request_size = configurations['facebook_page_per_request']
	facebook_pages_request_end = ( facebook_pages_request_index + facebook_pages_request_size ) % len( facebook_page_list )

	facebook_pages = []
	while facebook_pages_request_index != facebook_pages_request_end:
		facebook_pages.append( facebook_page_list[ facebook_pages_request_index ] )
		logger.info( '{}: {}'.format( facebook_pages_request_index, facebook_page_list[facebook_pages_request_index] ) )
		facebook_pages_request_index = ( facebook_pages_request_index + 1 ) % len( facebook_page_list )


def periodicPullFromFacebook(bot, job):
	"""
	Checks for new posts for every page in the list loaded from the
	configurations file, posts them, and updates the dates.json file, which
	contains the date for the latest post posted to Telegram for every
	page.
	"""
	global last_update_records

	updateFacebookPageListForRequest()
	createNextFacebookJob( bot )

	chat_id = job.context
	logger.info('Accessing Facebook...')

	page_field = [	'name', 'posts' ]
	post_field = [	'created_time',
					'type',
					'message',
					'full_picture',
					'story',
					'source',
					'link',
					'caption',
					'parent_id',
					'object_id',
					'permalink_url' ]
	request_field = ','.join( page_field ) \
					+ '{}{}{}'.format( '{', ','.join( post_field ), '}' )
	logger.info( 'requesting field: {}'.format( request_field ) )

	try:
		#Request to the GraphAPI with all the pages (list) and required fields
		facebook_fetch_result = facebook_graph.get_objects( \
			ids=facebook_pages, \
			fields = request_field, \
			locale=configurations['locale'] )
		logger.info('Successfully fetched Facebook posts.')

	except facebook.GraphAPIError as err:
		logger.error( 'Could not get Facebook posts.' )
		logger.error( 'Message: {}'.format( err.message ) )
		logger.error( 'Type: {}'.format( err.type ) )
		logger.error( 'Code: {}'.format( err.code ) )
		logger.error( 'Result: {}'.format( err.result ) )

		# Send a message of error to the channel
		msg = 'Could not get facebook posts.\nMessage: {}\nType: {}\nCode: {}\nResult:{}'.format(err.message, err.type, err.code, err.result)
		bot.send_message( chat_id = chat_id, text=msg )

		# Extends the refresh rate no matter what the error is.
		configurations['facebook_refresh_rate'] *= 2
		logger.error( 'Extend refresh rate to {}.'.format( configurations['facebook_refresh_rate'] ) )
		return

	except Exception as err:
		# In case there are errors other than facebook's error
		logger.error( 'Unknown Error' )
		bot.send_message( chat_id = chat_id, text = 'Unknown Exception' )
		bot.send_message( chat_id = chat_id, text = str( err )  )
		return

	logger.info( 'Fetching from facebook completes.  The next pulling job should happens in {} seconds.'.format( configurations['facebook_refresh_rate'] ) )

	new_posts_list = filterNewPosts( facebook_pages, facebook_fetch_result, last_update_records )
	postNewPosts( new_posts_list, chat_id )

	# By switching the show_usage_limit_status can tell you the business
	# of your facebook token
	if show_usage_limit_status:
		rateLimitStatus = getRateLimitStatus()
		msg = '=== Rate Limit Status ===\ncall_count: {}\ntotal_time: {}\ntotal_cputime: {}'.format(
			rateLimitStatus['call_count'], rateLimitStatus['total_time'], rateLimitStatus['total_cputime'] )
		bot.send_message( chat_id = chat_id, text = msg )
	logger.info( 'The bot has posted all the new posts from this fetch.' )


def createNextFacebookJob( bot ):
	"""Create and schedule the next job for pulling the up-to-date posts
	of the pages from the facebook.  We adjust the scheduling time to
	prevent the bot makes too many request within a short period.
	"""
	global facebook_job

	configurations['facebook_refresh_rate'] -= 230.0

	# The refresh rate should between the minimal value and 3600.
	# Facebook calculates the business with 1-hour time window.
	configurations['facebook_refresh_rate'] = min( configurations['facebook_refresh_rate'], 3600.0 )
	configurations['facebook_refresh_rate'] = max( \
			configurations['facebook_refresh_rate'], \
			configurations['facebook_refresh_rate_default'] )

	facebook_job = telegram_job_queue.run_once( \
						periodicPullFromFacebook, \
						configurations['facebook_refresh_rate'], \
						context = configurations['channel_id'] )
	logger.info( 'The next checking job will be in {} seconds'.format( configurations['facebook_refresh_rate'] ) )


def error(bot, update, error):
	logger.warn('Update "{}" caused error "{}"'.format(update, error))

def getRateLimitStatus():
	"""Get the current facebook Rait Limit"""

	url = 'https://graph.facebook.com/v3.0/me'
	args = { 'access_token': configurations['facebook_token'] }
	respond = requests.get( url, params = args )

	return json.loads( respond.headers['x-app-usage'] )

# ======================================================== #

# ----- Handlers ----- #

class BotControlHandler:
	# All the bot's commands are placed here.

	@staticmethod
	def setupBotHandlers( bot_dispatcher ):
		# An easy way to setup the handlers of a bot.
		bot_dispatcher.add_handler( CommandHandler( 'status', BotControlHandler.statusHandler ) )
		bot_dispatcher.add_handler( CommandHandler( 'extend', BotControlHandler.extendHandler ) )
		bot_dispatcher.add_handler( CommandHandler( 'start', BotControlHandler.startHandler ) )
		bot_dispatcher.add_handler( CommandHandler( 'reduce', BotControlHandler.reduceHandler ) )
		bot_dispatcher.add_handler( CommandHandler( 'reset', BotControlHandler.resetHandler ) )
		bot_dispatcher.add_handler( CommandHandler( 'toggle', BotControlHandler.toggleRateLimitStatus ) )
		bot_dispatcher.add_handler( MessageHandler( Filters.text, BotControlHandler.echoHandler ) )

	@staticmethod
	def statusHandler( bot, update ):
		rateLimitStatus = getRateLimitStatus()
		msg = 'Refresh Rate: {:.2f} minutes\ncall_count: {}\ntotal_time: {}\ntotal_cputime: {}'.format(
			configurations['facebook_refresh_rate']/60,
			rateLimitStatus['call_count'],
			rateLimitStatus['total_time'],
			rateLimitStatus['total_cputime']
		)
		bot.send_message( chat_id = update.message.chat_id, text = msg )

	@staticmethod
	def startHandler( bot, update ):
		msg = str.format(
			'The bot has started.'
		)
		bot.send_message( chat_id = update.message.chat_id, text = msg )

	@staticmethod
	def extendHandler( bot, update ):
		configurations['facebook_refresh_rate'] = configurations['facebook_refresh_rate'] * 4
		msg = str.format(
			'Extending the refresh rate to {:.2f} minutes',
			configurations['facebook_refresh_rate']/60.0
		)
		bot.send_message( chat_id = update.message.chat_id, text = msg )

	@staticmethod
	def resetHandler( bot, update ):
		configurations['facebook_refresh_rate'] = configurations['facebook_refresh_rate_default']
		msg = 'Reset refresh rate to {:.2f} minutes'.format( configurations['facebook_refresh_rate']/60.0 )
		bot.send_message( chat_id = update.message.chat_id, text = msg )

	@staticmethod
	def reduceHandler( bot, update ):
		configurations['facebook_refresh_rate'] -= 250.0
		msg = 'Reduce refresh rate to {:.2f} minutes'.format( configurations['facebook_refresh_rate']/60.0 )
		bot.send_message( chat_id = update.message.chat_id, text = msg )

	@staticmethod
	def toggleRateLimitStatus( bot, update ):
		global show_usage_limit_status
		msg = '{} Rate Limit Status while updating.'.format( 'Hide' if show_usage_limit_status else 'Show' )
		show_usage_limit_status = not show_usage_limit_status
		bot.send_message( chat_id = update.message.chat_id, text = msg )

	@staticmethod
	def echoHandler( bot, update ):
		bot.send_message( chat_id = update.message.chat_id, text = 'Echo: {}'.format( update.message.text ) )

	@staticmethod
	def getRateLimitStatus():
		"""Get the current facebook Rait Limit"""

		url = 'https://graph.facebook.com/v3.0/me'
		args = { 'access_token': configurations['facebook_token'] }
		respond = requests.get( url, params = args )

		return json.loads( respond.headers['x-app-usage'] )



# ======================================================== #

# ----- The main function #

def main():
	global facebook_pages
	global working_directory
	global last_update_record_file
	global facebook_job

	# Setting file directories
	working_directory = path.dirname(path.realpath(__file__))
	last_update_record_file = working_directory + '/dates.json'

	# Load Configurations
	loadConfiguration( working_directory + '/botsettings.ini' )
	loadFacebookGraph(configurations['facebook_token'])
	loadTelegramBot(configurations['telegram_token'])
	facebook_pages = configurations['facebook_pages']

	# Log all errors
	telegram_dispatcher.add_error_handler(error)

	# Add Handlers
	BotControlHandler.setupBotHandlers( telegram_dispatcher )

	# Start process commands from users
	telegram_updater.start_polling()

	# Use checkNewPagesExistness to check if page is new added
	checkNewPagesExistness( facebook_pages )

	# Execute the job as soon as possible.
	facebook_job = telegram_job_queue.run_once( \
					periodicPullFromFacebook, 0, \
					context = configurations['channel_id'] )

	# Enter Idle state
	telegram_updater.idle()


if __name__ == '__main__':
	main()

