#!/usr/bin/env python
#  -*- coding: utf-8 -*-

#####################
# ABOUT THIS SCRIPT #
#####################
#
# TalosBlacklistImporter.py
# ----------------
# Author: Alan Nix
# Property of: Cisco Systems
# Version: 1.0
# Release Date: 06/01/2019
#
############################################################

# import datetime
import getpass
import json
import os
import time

import requests

from requests.packages import urllib3
from requests.auth import HTTPBasicAuth

# If receiving SSL Certificate Errors, un-comment the line below
urllib3.disable_warnings()

# Setup an API session
API_SESSION = requests.Session()

# Config Paramters
CONFIG_FILE = "config.json"
CONFIG_DATA = {}

# Talos Blacklist Cache
TALOS_DATA_FILE = "blacklist.json"

####################
#    FUNCTIONS     #
####################


def load_config():
    """Load configuration data from file"""

    print("Loading configuration data...")

    # If we have a stored config file, then use it, otherwise terminate
    if os.path.isfile(CONFIG_FILE):

        # Open the CONFIG_FILE and load it
        with open(CONFIG_FILE, 'r') as config_file:
            CONFIG_DATA = json.loads(config_file.read())

        print("Configuration data loaded successfully.")

        return CONFIG_DATA

    else:
        print("The configuration file \"{}\" was not found.".format(CONFIG_FILE))
        exit()


def save_config():
    """Save configuration data to file"""

    with open(CONFIG_FILE, 'w') as output_file:
        json.dump(CONFIG_DATA, output_file, indent=4)


def get_blacklist():
    """Retrieve the Talos Blacklist and return a list of IPs"""

    try:
        # Get the IP Blacklist data from Talos
        response = requests.get(CONFIG_DATA["TALOS_BLACKLIST_URL"], stream=True)

        # If the request was successful
        if response.status_code >= 200 or response.status_code < 300:

            # A placeholder list for IPs
            ip_list = []

            # Add each IP address to our ip_list
            for line in response.iter_lines():

                # Decode the line
                line = line.decode("utf-8")

                # Make sure we haven't exceeded our requests per minute maximum
                if "exceeded" in line:
                    print("It looks like we've exceeded the request maximum for the blacklist. Terminating.")
                    exit()

                if "DOCTYPE" in line:
                    print("Got garbage back from the Talos blacklist. Terminating.")
                    exit()

                if line:
                    ip_list.append(line)

            # Cache the data from Talos
            with open(TALOS_DATA_FILE, 'w') as output_file:
                json.dump(ip_list, output_file, indent=4)

            return ip_list

        else:
            print("Failed to get data from Talos. Terminating.")
            exit()

    except Exception as err:
        print("Unable to get the Talos Blacklist - Error: {}".format(err))
        exit()


def get_access_token():
    """Get an Access Token from the Stealthwatch API"""

    print("Authenticating to Stealthwatch...")

    # The URL to authenticate to the SMC
    url = "https://{}/token/v2/authenticate".format(CONFIG_DATA["SW_ADDRESS"])

    print("Stealthwatch Authentication URL: {}".format(url))

    # JSON to hold the authentication credentials
    login_credentials = {
        "username": CONFIG_DATA["SW_USERNAME"],
        "password": CONFIG_DATA["SW_PASSWORD"]
    }

    try:
        # Make an authentication request to the SMC
        response = API_SESSION.post(url, data=login_credentials, verify=False)

        # If the request was successful, then proceed
        if response.status_code == 200:
            print("Successfully Authenticated.")

            return response.text

        else:
            print("SMC Connection Failure - HTTP Return Code: {}\nResponse: {}".format(response.status_code, response.text))
            exit()

    except Exception as err:
        print("Unable to post to the SMC - Error: {}".format(err))
        exit()


def get_tenants():
    """Get the "tenants" (domains) from Stealthwatch"""

    print("Fetching Stealthwatch Tenants...")

    # The URL to get tenants
    url = "https://{}/sw-reporting/v1/tenants/".format(CONFIG_DATA["SW_ADDRESS"])

    print("Stealthwatch Tenant URL: {}".format(url))

    try:
        # Get the tenants from Stealthwatch
        response = API_SESSION.get(url, verify=False)

        # If the request was successful, then proceed, otherwise terminate.
        if response.status_code == 200:

            # Parse the response as JSON
            tenants = response.json()["data"]

            # Set the Domain ID if theres only one, or prompt the user if there are multiple
            if len(tenants) == 1:
                selected_tenant_id = tenants[0]["id"]
            else:
                selected_item = selection_list("Tenants", "displayName", tenants)
                selected_tenant_id = selected_item["id"]

            return selected_tenant_id

        else:
            print("SMC Connection Failure - HTTP Return Code: {}\nResponse: {}".format(response.status_code, response.text))
            exit()

    except Exception as err:
        print("Unable to post to the SMC - Error: {}".format(err))
        exit()


def create_update_tag(ip_list):
    """Create/Update a Tag (Host Group) in Stealthwatch"""

    # Build the URL to create a Tag
    url = "https://{}/smc-configuration/rest/v1/tenants/{}/tags/".format(CONFIG_DATA["SW_ADDRESS"],
                                                                         CONFIG_DATA["SW_TENANT_ID"])

    data = [{
        "name": "Talos Blacklist",
        "ranges": ip_list,
        "hostBaselines": False,
        "suppressExcludedServices": True,
        "inverseSuppression": False,
        "hostTrap": False,
        "sendToCta": False,
        "location": "OUTSIDE",
        "parentId": 0
    }]

    try:
        # If we already have a Tag ID, just update, otherwise create.
        if CONFIG_DATA["SW_TAG_ID"]:
            print("Updating Stealthwatch Tag {}...".format(CONFIG_DATA["SW_TAG_ID"]))

            # Add the Tag ID to the data
            data[0]["id"] = CONFIG_DATA["SW_TAG_ID"]

            # Send the update request
            response = API_SESSION.put(url, json=data, verify=False)

        else:
            print("Creating Stealthwatch Tag...")

            # Send the create request
            response = API_SESSION.post(url, json=data, verify=False)

        # If the request was successful, then proceed, otherwise terminate.
        if response.status_code == 200:
            print("Tag Request Successful.")

            # Parse the response as JSON
            tag_id = response.json()["data"][0]["id"]

            # Return the Tag ID
            return tag_id

        else:
            print("SMC Connection Failure - HTTP Return Code: {}\nResponse: {}".format(response.status_code, response.text))
            exit()

    except Exception as err:
        print("Unable to post to the SMC - Error: {}".format(err))
        exit()


def create_cse():
    """Create a Custom Security Event for bi-directional traffic to/from a Blacklisted IP"""

    print("Creating Custom Security Event...")

    # Build the URL to create a CSE
    url = "https://{}/smc-configuration/rest/v1/tenants/{}/policy/customEvents".format(CONFIG_DATA["SW_ADDRESS"],
                                                                                       CONFIG_DATA["SW_TENANT_ID"])

    data = {
        "name": "CSE: Talos Blacklist",
        "subject": {
            "tags": {
                "includes": [1]
            },
            "packets": {
                "value": 1,
                "operator": "GREATER-THAN-OR-EQUAL"
            },
            "orientation": "either"
        },
        "peer": {
            "tags": {
                "includes": [CONFIG_DATA['SW_TAG_ID']]
            },
            "packets": {
                "value": 1,
                "operator": "GREATER-THAN-OR-EQUAL"
            }
        }
    }

    try:
        # Send the create request
        response = API_SESSION.post(url, json=data, verify=False)

        # If the request was successful, then proceed, otherwise terminate.
        if response.status_code == 200:
            print("Custom Security Event Successfully Created.")

            # Parse the response as JSON
            cse_id = response.json()["data"]["customSecurityEvents"]["id"]

            # Return the Tag ID
            return cse_id

        else:
            print("SMC Connection Failure - HTTP Return Code: {}\nResponse: {}".format(response.status_code, response.text))
            exit()

    except Exception as err:
        print("Unable to post to the SMC - Error: {}".format(err))
        exit()


def get_cse():
    """Get the Custom Security Event"""

    print("Fetching the Custom Security Event...")

    # Build the URL to create a CSE
    url = "https://{}/smc-configuration/rest/v1/tenants/{}/policy/customEvents/{}".format(CONFIG_DATA["SW_ADDRESS"],
                                                                                          CONFIG_DATA["SW_TENANT_ID"],
                                                                                          CONFIG_DATA["SW_CSE_ID"])

    try:
        # Send the get request
        response = API_SESSION.get(url, verify=False)

        # If the request was successful, then proceed, otherwise terminate.
        if response.status_code == 200:
            print("Custom Security Event Successfully Fetched.")

            # Parse the response as JSON
            cse_data = response.json()["data"]["customSecurityEvents"]

            # Return the CSE data
            return cse_data

        else:
            print("SMC Connection Failure - HTTP Return Code: {}\nResponse: {}".format(response.status_code, response.text))
            exit()

    except Exception as err:
        print("Unable to post to the SMC - Error: {}".format(err))
        exit()


def enable_cse():
    """Enable the Custom Security Event"""

    print("Enabling the Custom Security Event...")

    # Fetch the CSE
    cse_data = get_cse()

    # Build the URL to enable the CSE
    url = "https://{}/smc-configuration/rest/v1/tenants/{}/policy/customEvents/{}/enable".format(CONFIG_DATA["SW_ADDRESS"],
                                                                                                 CONFIG_DATA["SW_TENANT_ID"],
                                                                                                 CONFIG_DATA["SW_CSE_ID"])

    data = {
        "timestamp": cse_data["timestamp"]
    }

    try:
        # Send the get request
        response = API_SESSION.put(url, json=data, verify=False)

        # If the request was successful, then proceed, otherwise terminate.
        if response.status_code == 200:
            print("Custom Security Event Successfully Enabled.")

            return None

        else:
            print("SMC Connection Failure - HTTP Return Code: {}\nResponse: {}".format(response.status_code, response.text))
            exit()

    except Exception as err:
        print("Unable to post to the SMC - Error: {}".format(err))
        exit()


def selection_list(item_name, item_name_key, item_dict):
    """This is a function to allow users to select an item from a dict."""

    print("\nPlease select one of the following {}:\n".format(item_name))

    index = 1

    # Print the options that are available
    for item in item_dict:
        print("\t{}) {}".format(index, item[item_name_key]))
        index += 1

    # Prompt the user for the item
    selected_item = input("\n{} Selection: ".format(item_name))

    # Make sure that the selected item was valid
    if 0 < int(selected_item) <= len(item_dict):
        selected_item = int(selected_item) - 1
    else:
        print("ERROR: {} selection was not correct.".format(item_name))
        exit()

    return item_dict[selected_item]


####################
# !!! DO WORK !!!  #
####################


if __name__ == "__main__":

    # Load configuration data from file
    CONFIG_DATA = load_config()

    # If not hard coded, get the SMC Address, Username and Password
    if not CONFIG_DATA["SW_ADDRESS"]:
        CONFIG_DATA["SW_ADDRESS"] = input("Stealthwatch IP/FQDN Address: ")
        save_config()
    if not CONFIG_DATA["SW_USERNAME"]:
        CONFIG_DATA["SW_USERNAME"] = input("Stealthwatch Username: ")
        save_config()
    if not CONFIG_DATA["SW_PASSWORD"]:
        CONFIG_DATA["SW_PASSWORD"] = getpass.getpass("Stealthwatch Password: ")
        save_config()

    # Authenticate to Stealthwatch API
    get_access_token()

    # If a Domain ID wasn't specified, then get one
    if not CONFIG_DATA["SW_TENANT_ID"]:

        # Get Tenants from REST API
        CONFIG_DATA["SW_TENANT_ID"] = get_tenants()

        # Save the Tenant/Domain ID
        save_config()

    # Create a list to hold IPs
    ip_list = []

    # Check to see if we have cached data
    if os.path.isfile(TALOS_DATA_FILE):
        print("Cached blacklist found.")

        # Get the delta of the current time and the file modified time
        time_delta = time.time() - os.path.getmtime(TALOS_DATA_FILE)

        # If the file is less than an hour old, use it
        if time_delta < 3600:
            print("Cached blacklist was less than an hour old.  Using it.")

            # Open the CONFIG_FILE and load it
            with open(TALOS_DATA_FILE, 'r') as blacklist_file:
                ip_list = json.load(blacklist_file)

        else:
            print("Cached blacklist was too old, getting a new one.")

            # Get a new blacklist
            ip_list = get_blacklist()

    else:
        print("No cached blacklist was found, getting a new one.")

        # Get a new blacklist
        ip_list = get_blacklist()

    # Send the request to Stealthwatch to Create or Update the Tag (Host Group)
    CONFIG_DATA["SW_TAG_ID"] = create_update_tag(ip_list)
    save_config()

    # If the configuration dictates that we want to create a CSE
    if CONFIG_DATA["SW_CREATE_CSE"]:

        # If we haven't already created a CSE
        if not CONFIG_DATA["SW_CSE_ID"]:

            # Create the Custom Security Event
            CONFIG_DATA["SW_CSE_ID"] = create_cse()
            save_config()

            # Enable the Custom Security Event
            enable_cse()