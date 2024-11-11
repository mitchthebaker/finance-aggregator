import json
from flask import Flask, jsonify, request

app = Flask(__name__)

@app.route('/', methods=['GET'])
def get_root():
  arr = ["endpoint works as expected"]
  return jsonify(arr)

if __name__ == '__main__':
  app.run(port=3101)