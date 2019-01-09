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
workingDirectory = None
lastUpdateRecordFile = None
lastUpdateRecords = {}
request_seq = 0
show_usage_limit_status = False

# ----- Facebook Global Variables ----- #
facebook_graph = None
facebook_pages = None
facebook_job = None

# ----- Telegram Global Variables ----- #
bot = None
updater = None
dispatcher = None
job_queue = None

# -------------------------------------------------------- #


def loadConfiguration(filename):
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
		sys.exit('Fatal Error: Missing or invalid configurations file.')
	except configparser.NoOptionError:
		sys.exit('Fatal Error: Missing or invalid option in configurations file.')
	except ValueError:
		sys.exit('Fatal Error: Missing or invalid value in configurations file.')
	except SyntaxError:
		sys.exit('Fatal Error: Syntax error in page list.')


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


def loadFacebookGraph(facebook_token):
	"""
	Initialize Facebook GraphAPI with the token loaded from the configurations file
	"""

	global facebook_graph
	facebook_graph = facebook.GraphAPI( access_token = facebook_token, version = '3.0', timeout = 120 )


def loadTelegramBot(telegram_token):
	"""
	Initialize Telegram Bot API with the token loaded from the configurations file
	"""
	global bot
	global updater
	global dispatcher
	global job_queue

	try:
		bot = telegram.Bot(token=telegram_token)
	except InvalidToken:
	   sys.exit('Fatal Error: Invalid Telegram Token')

	updater = Updater(token=telegram_token)
	dispatcher = updater.dispatcher
	job_queue = updater.job_queue


def parsePostDate(post):
	"""
	Converts 'created_time' str from a Facebook post to the 'datetime' format
	"""
	date_format = "%Y-%m-%dT%H:%M:%S+0000"
	post_date = datetime.strptime(post['created_time'], date_format)
	return post_date


class dateTimeEncoder(json.JSONEncoder):
	"""
	Converts the 'datetime' type to an ISO timestamp for the JSON dumper
	"""
	def default(self, o):
		if isinstance(o, datetime):
			serial = o.isoformat()  #Save in ISO format
			return serial

		return super().default( o )



def dateTimeDecoder(pairs, date_format="%Y-%m-%dT%H:%M:%S"):
	"""
	Converts the ISO timestamp to 'datetime' type for the JSON loader
	"""
	d = {}

	for k, v in pairs:
		if isinstance(v, str):
			try:
				d[k] = datetime.strptime(v, date_format)
			except ValueError:
				d[k] = v
		else:
			d[k] = v

	return d


def loadDatesJSON( filename ):
	"""
	Loads the .json file containing the latest post's date for every page
	loaded from the configurations file to the 'lastUpdateRecords' dict
	"""
	with open( filename, 'r' ) as f:
		loaded_json = json.load( f, object_pairs_hook = dateTimeDecoder )

	logger.info( 'Loaded JSON file.' )
	return loaded_json


def dumpDatesJSON(lastUpdateRecords, filename):
	"""
	Dumps the 'lastUpdateRecords' dict to a .json file containing the
	latest post's date for every page loaded from the configurations file.
	"""
	with open(filename, 'w') as f:
		json.dump(lastUpdateRecords, f,
				  sort_keys=True, indent=4, cls=dateTimeEncoder)

	logger.info('Dumped JSON file.')
	return True


def getMostRecentPostsDates(facebook_pages, filename):
	"""
	Gets the date for the most recent post for every page loaded from the
	configurations file. If there is a 'dates.json' file, load it. If not, fetch
	the dates from Facebook and store them in the 'dates.json' file.
	The .json file is used to keep track of the latest posts posted to
	Telegram in case the bot is restarted after being down for a while.
	"""
	logger.info('Getting most recent posts dates...')

	global lastUpdateRecords

	# Check if dates.json exists.  If not, create one.
	try:
		lastUpdateRecords = loadDatesJSON( filename )
	except (IOError, ValueError):
		lastUpdateRecords = {}
		dumpDatesJSON( lastUpdateRecords, filename )

	# Check if any new page ID is added
	new_facebook_pages = []

	for page in facebook_pages:
		if page not in lastUpdateRecords:
			new_facebook_pages.append( page )
			logger.info( 'Checking if page {} went online...'.format( page ) )

	if len( new_facebook_pages ) == 0:
		return

	last_posts = facebook_graph.get_objects(
			ids = new_facebook_pages,
			fields = 'name,posts.limit(1){created_time}'
	)

	for page in new_facebook_pages:
		try:
			last_post = last_posts[page]['posts']['data'][0]
			lastUpdateRecords[page] = parsePostDate( last_post )
			dumpDatesJSON( lastUpdateRecords, filename )
			logger.info( 'Page {} ({}) went online.'.format( last_posts[page]['name'], page ) )

		except KeyError:
			logger.warning( 'Page {} not found.'.format( page ) )



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
			request.urlretrieve(direct_link, workingDirectory+'/temp.jpg')
			logger.info('Sending file...')
			with open(workingDirectory+'/temp.jpg', 'rb') as picture:
				message = bot.send_photo(
					chat_id=chat_id,
					photo=picture,
					caption=post_message,
					timeout=120)
			remove(workingDirectory+'/temp.jpg')   #Delete the temp picture
			return message

		except TimedOut:
			"""
			If there is a timeout, try again with a higher
			timeout value for 'bot.send_photo'
			"""
			logger.warning('File upload timed out, trying again...')
			logger.info('Sending file...')
			with open(workingDirectory+'/temp.jpg', 'rb') as picture:
				message = bot.send_photo(
					chat_id=chat_id,
					photo=picture,
					caption=post_message,
					timeout=200)
			remove(workingDirectory+'/temp.jpg')   #Delete the temp picture
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
											workingDirectory+'/temp.mp4')
						logger.info('Sending file...')
						with open(workingDirectory+'/temp.mp4', 'rb') as video:
							message = bot.send_video(
								chat_id=chat_id,
								video=video,
								caption=post_message,
								timeout=120)
						remove(workingDirectory+'/temp.mp4')   #Delete the temp video
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

	"""
	If there's a message in the post, and it's allowed by the
	configurations file, store it in 'post_message', which will be passed to
	another function based on the post type.
	"""
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

# Check if the first message is posted to telegram
# If posted, and encounter error afterward, the update is considered as posted
# Otherwise, it may be the error from telegram, and should be posted again.
headerPosted = False

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
	global lastUpdateRecords
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
			postToChat(post, bot, chat_id)
		except BadRequest as e:
			logger.error('Error: Telegram could not send the message')
			logger.error('Message: {}'.format(e.message))
			bot.send_message( chat_id = chat_id, text = 'Bad Request Exception')
			#raise
		except KeyError:
			logger.error('Error: Got Key Error, ignore the post from {}'.format(post['pagename']))
			logger.exception(' ')
			bot.send_message( chat_id = chat_id, text = 'Key Error Exception from page {}'.format(post['pagename']))
			headerPosted = True
		except Exception as e:
			msg = 'Unknown Error: {} when processing page {}'.format( type(e), posts_page )
			logger.error(msg)
			bot.send_message( chat_id = chat_id, text = msg )
		finally:
			if headerPosted:
				lastUpdateRecords[posts_page] = parsePostDate(post)
				dumpDatesJSON(lastUpdateRecords, lastUpdateRecordFile)
				post_left -= 1
			bot.send_message( chat_id = chat_id, text = '{} post(s) left'.format(post_left) )

		logger.info('Waiting {} seconds before next post...'.format(time_to_sleep))
		sleep(int(time_to_sleep))
	logger.info('{} posts posted to Telegram'.format(new_posts_total_count))


def getNewPosts(facebook_pages, pages_dict, lastUpdateRecords):
	#Iterate every page in the list loaded from the configurations file
	new_posts_total = []
	for page in facebook_pages:
		try:
			logger.info('Getting list of posts for page {}...'.format(
													pages_dict[page]['name']))

			#List of last 25 posts for current page. Every post is a dict.
			posts_data = pages_dict[page]['posts']['data']

			#List of posts posted after "last posted date" for current page
			new_posts = list(filter(
				lambda post: parsePostDate(post) > lastUpdateRecords[page],
				posts_data))

			if not new_posts:
				logger.info('No new posts for this page.')
				continue    #Goes to next iteration (page)
			else:
				logger.info('Found {} new posts for this page.'.format(len(new_posts)))
				for post in new_posts: #For later identification
					post['page'] = page
					post['pagename'] = pages_dict[page]['name']
				new_posts_total = new_posts_total + new_posts
		#If 'page' is not present in 'pages_dict' returned by the GraphAPI
		except KeyError:
			logger.warning('Page not found: {}'.format( page ) )
			continue
	logger.info('Checked all pages.')

	#Sorts the list of new posts in chronological order
	new_posts_total.sort(key=lambda post: parsePostDate(post))
	logger.info('Sorted posts by chronological order.')

	return new_posts_total

def updateRequestList():
	global request_seq
	global facebook_pages

	facebook_page_list = configurations['facebook_pages']

	request_size = configurations['facebook_page_per_request']
	request_end = (request_seq + request_size) % len(facebook_page_list)

	facebook_pages = []
	while request_seq != request_end:
		facebook_pages.append( facebook_page_list[ request_seq ] )
		logger.info('{}: {}'.format(request_seq, facebook_page_list[ request_seq ] ) )
		request_seq = (request_seq + 1) % len(facebook_page_list)

def periodicCheck(bot, job):
	"""
	Checks for new posts for every page in the list loaded from the
	configurations file, posts them, and updates the dates.json file, which
	contains the date for the latest post posted to Telegram for every
	page.
	"""

	updateRequestList()
	createCheckJob( bot )

	global lastUpdateRecords
	chat_id = job.context
	logger.info('Accessing Facebook...')

	try:
		#Request to the GraphAPI with all the pages (list) and required fields
		pages_dict = facebook_graph.get_objects(
			ids=facebook_pages,
			fields='name,\
					posts{\
						  created_time,type,message,full_picture,story,\
						  source,link,caption,parent_id,object_id,permalink_url}',
			locale=configurations['locale'])

		logger.info('Successfully fetched Facebook posts.')

	#Error in the Facebook API
	except facebook.GraphAPIError as err:
		logger.error('Could not get Facebook posts.')
		logger.error('Message: {}'.format(err.message))
		logger.error('Type: {}'.format(err.type))
		logger.error('Code: {}'.format(err.code))
		logger.error('Result: {}'.format(err.result))
		msg = 'Could not get facebook posts.\nMessage: {}\nType: {}\nCode: {}\nResult:{}'.format(err.message, err.type, err.code, err.result)
		bot.send_message( chat_id = chat_id, text=msg )

		# Extends the refresh rate
		configurations['facebook_refresh_rate'] *= 2
		logger.error( 'Extend refresh rate to {}.'.format( configurations['facebook_refresh_rate'] ) )

		"""
		TODO: 'get_object' for every page individually, due to a bug
		in the Graph API that makes some pages return an OAuthException 1,
		which in turn doesn't allow the 'get_objects' method return a dict
		that has only the working pages, which is the expected behavior
		when one or more pages in 'facbeook_pages' are offline. One possible
		workaround is to create an Extended Page Access Token instad of an
		App Token, with the downside of having to renew it every two months.
		"""
		return
	except Exception as err:
		logger.error( 'Unknown Error' )
		bot.send_message( chat_id = chat_id, text = 'Unknown Exception' )
		bot.send_message( chat_id = chat_id, text = str( err )  )
		return

	new_posts_total = getNewPosts(facebook_pages, pages_dict, lastUpdateRecords)

	logger.info('Checked all posts. Next check in '
		  +str(configurations['facebook_refresh_rate'])
		  +' seconds.')

	postNewPosts(new_posts_total, chat_id)

	if new_posts_total:
		logger.info('Posted all new posts.')
	else:
		logger.info('No new posts.')

	if show_usage_limit_status:
		rateLimitStatus = getRateLimitStatus()
		msg = '=== Rate Limit Status ===\ncall_count: {}\ntotal_time: {}\ntotal_cputime: {}'.format(
			rateLimitStatus['call_count'],
			rateLimitStatus['total_time'],
			rateLimitStatus['total_cputime']
		)
		bot.send_message( chat_id = chat_id, text = msg )

def createCheckJob(bot):
	"""
	Creates a job that periodically calls the 'periodicCheck' function
	"""
	global facebook_job

	configurations['facebook_refresh_rate'] -= 230.0

	if configurations['facebook_refresh_rate'] > 3600:
		configurations['facebook_refresh_rate'] = 3600
	elif configurations['facebook_refresh_rate'] < configurations['facebook_refresh_rate_default']:
		configurations['facebook_refresh_rate'] = configurations['facebook_refresh_rate_default']

	facebook_job = job_queue.run_once( periodicCheck, configurations['facebook_refresh_rate'], context = configurations['channel_id'] )

	logger.info('Job created.')


def error(bot, update, error):
	logger.warn('Update "{}" caused error "{}"'.format(update, error))

def statusHandler( bot, update ):
	rateLimitStatus = getRateLimitStatus()
	msg = 'Refresh Rate: {:.2f} minutes\ncall_count: {}\ntotal_time: {}\ntotal_cputime: {}'.format(
		configurations['facebook_refresh_rate']/60,
		rateLimitStatus['call_count'],
		rateLimitStatus['total_time'],
		rateLimitStatus['total_cputime']
	)
	bot.send_message( chat_id = update.message.chat_id, text = msg )

def startHandler( bot, update ):
	msg = str.format(
		'The bot has started.'
	)
	bot.send_message( chat_id = update.message.chat_id, text = msg )

def extendHandler( bot, update ):
	configurations['facebook_refresh_rate'] = configurations['facebook_refresh_rate'] * 4
	msg = str.format(
		'Extending the refresh rate to {:.2f} minutes',
		configurations['facebook_refresh_rate']/60.0
	)
	bot.send_message( chat_id = update.message.chat_id, text = msg )

def resetHandler( bot, update ):
	configurations['facebook_refresh_rate'] = configurations['facebook_refresh_rate_default']
	msg = 'Reset refresh rate to {:.2f} minutes'.format( configurations['facebook_refresh_rate']/60.0 )
	bot.send_message( chat_id = update.message.chat_id, text = msg )

def reduceHandler( bot, update ):
	configurations['facebook_refresh_rate'] -= 250.0
	msg = 'Reduce refresh rate to {:.2f} minutes'.format( configurations['facebook_refresh_rate']/60.0 )
	bot.send_message( chat_id = update.message.chat_id, text = msg )

def toggleRateLimitStatus( bot, update ):
	global show_usage_limit_status
	msg = '{} Rate Limit Status while updating.'.format( 'Hide' if show_usage_limit_status else 'Show' )
	show_usage_limit_status = not show_usage_limit_status
	bot.send_message( chat_id = update.message.chat_id, text = msg )

def echoHandler( bot, update ):
	bot.send_message( chat_id = update.message.chat_id, text = 'Echo: {}'.format( update.message.text ) )

def getRateLimitStatus():
	url = 'https://graph.facebook.com/v3.0/me'
	args = { 'access_token': configurations['facebook_token'] }
	respond = requests.get( url, params = args )

	rateLimitStatus = json.loads( respond.headers['x-app-usage'] )
	return rateLimitStatus


def main():
	global facebook_pages
	global workingDirectory
	global lastUpdateRecordFile
	global facebook_job

	workingDirectory = path.dirname(path.realpath(__file__))
	lastUpdateRecordFile = workingDirectory + '/dates.json'

	loadConfiguration( workingDirectory + '/botsettings.ini' )
	loadFacebookGraph(configurations['facebook_token'])
	loadTelegramBot(configurations['telegram_token'])
	facebook_pages = configurations['facebook_pages']


	# Test if new page added
	startPage = 0
	while startPage < len(facebook_pages):
		endPage = (startPage + 40) if ( (startPage + 40) < len(facebook_pages) ) else len(facebook_pages)
		getMostRecentPostsDates(facebook_pages[startPage:endPage], lastUpdateRecordFile)
		# facebook only allow requesting 50 pages at a time
		startPage += 40
		sleep(10)

	facebook_job = job_queue.run_once( periodicCheck, 0, context = configurations['channel_id'] )

	#Log all errors
	dispatcher.add_handler( CommandHandler( 'status', statusHandler ) )
	dispatcher.add_handler( CommandHandler( 'extend', extendHandler ) )
	dispatcher.add_handler( CommandHandler( 'start', startHandler ) )
	dispatcher.add_handler( CommandHandler( 'reduce', reduceHandler ) )
	dispatcher.add_handler( CommandHandler( 'reset', resetHandler ) )
	dispatcher.add_handler( CommandHandler( 'toggle', toggleRateLimitStatus ) )
	dispatcher.add_handler( MessageHandler( Filters.text, echoHandler ) )
	dispatcher.add_error_handler(error)

	updater.start_polling()
	updater.idle()


if __name__ == '__main__':
	main()

