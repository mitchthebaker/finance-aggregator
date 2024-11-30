from plaid.api import plaid_api
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.products import Products
from flask import Blueprint, request, jsonify, abort
from cache import cache
from database import mongo
from database.utils import convert_arbitrary_type_to_dict, assign_document_id

import plaid
import json
import config

plaid_blueprint = Blueprint('plaid', __name__)

configuration = plaid.Configuration(
  host = plaid.Environment.Sandbox,
  api_key = {
    'clientId': config.PLAID_CLIENT_ID,
    'secret': config.PLAID_SECRET
  }
)
api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

@plaid_blueprint.route('/access_token/<institution_id>', methods=['POST'])
def access_token(institution_id):
  '''Retrieve a new access token from the Plaid API
    ---
    parameters:
      - name: institution_id
        description: The ID of the institution the Item will be associated with
        in: path
        type: string
        required: true
      - name: initial_products
        description: The products to initially pull for the Item. May be any products that the specified institution_id  supports.
        in: body
        items:
          type: string
        type: array
        required: true
    responses:
      200:
        description: JSON payload with access_token, item_id, and request_id
      400:
        description: initial_products is required and must be a non-empty list
  '''
  body = request.get_json()
  initial_products = body.get('initial_products')
  if not initial_products or not isinstance(initial_products, list):
    return jsonify({ 'error': 'initial_products is required and must be a non-empty list' }), 400
  
  try:
    products_list = [Products(product) for product in initial_products]
  except Exception as e:
    return jsonify({ 'error': f'Error creating Products objects: {str(e)}' }), 400

  pt_request = SandboxPublicTokenCreateRequest(
    institution_id = institution_id,
    initial_products = products_list
  )
  pt_response = client.sandbox_public_token_create(pt_request)
  public_token = pt_response["public_token"]

  exchange_request = ItemPublicTokenExchangeRequest(public_token = public_token)
  exchange_response = client.item_public_token_exchange(exchange_request)

  cache.set('plaid_access_token', exchange_response.access_token)
  json_string = json.dumps(exchange_response.to_dict(), default = str)
  return jsonify(json_string), 200
  

@plaid_blueprint.route('/transaction_sync', methods=['POST'])
def transaction_sync():
  '''Retrieve transactions corresponding to an access token for a specific institution id
    ---
    responses:
      200:
        description: JSON payload with added, modified, removed, accounts
      400:
        description: Missing Plaid access token
      500:
        description: Error adding into transactions collection
  '''
  if cache.get('plaid_access_token') is None:
    return jsonify({ 'error': 'Missing Plaid access token' }), 400

  if cache.get('cursor') is None:
    cursor = ''
  else:
    cursor = cache.get('cursor')

  # New transaction updates since "cursor"
  added = []
  modified = []
  removed = [] # Removed transaction ids
  has_more = True

  # Iterate through each page of new transaction updates for item
  while has_more:
    request = TransactionsSyncRequest(
      access_token = cache.get('plaid_access_token'),
      cursor = cursor,
      count = 100,
      options = {
        'days_requested': 60
      }
    )
    response = client.transactions_sync(request)

    # Add this page of results
    added.extend(response['added'])
    modified.extend(response['modified'])
    removed.extend(response['removed'])

    has_more = response['has_more']

    # Update cursor to the next cursor
    cursor = response['next_cursor']

  try:
    # Add item into mongodb collection
    transactions = convert_arbitrary_type_to_dict(added)
    assign_document_id(transactions, 'transaction_id')
    mongo.db[config.TRANSACTIONS_COLLECTION].insert_many(transactions)
  except Exception as e:
    abort(500, { 'error': f'Error adding into transactions collection: {str(e)}' })

  cache.set('cursor', cursor, timeout = 5)
  json_string = json.dumps(response.to_dict(), default = str)
  return jsonify(json_string), 200
