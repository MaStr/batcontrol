"""Test script to fetch solar forecast data from HomeAssistant via WebSocket

This script demonstrates how to connect to HomeAssistant and fetch solar forecast
data from the sensor.solar_forecast_ml_evcc_solar_prognose sensor.
"""

import asyncio
import datetime
import json
import logging

from websockets.asyncio.client import connect

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BASE_URL = "http://homeassistant.local:8123"
API_TOKEN = ""
ENTITY_ID = "sensor.solar_forecast_ml_evcc_solar_prognose"


async def websocket_connect(base_url: str, api_token: str):
    """Connect to HomeAssistant WebSocket API and authenticate

    Returns:
        Tuple of (websocket connection, message_id counter)

    Raises:
        RuntimeError: If connection or authentication fails
    """
    # Build WebSocket URL
    ws_url = base_url.replace('http://', 'ws://').replace('https://', 'wss://')
    ws_url = f"{ws_url}/api/websocket"

    logger.debug("Connecting to HomeAssistant WebSocket: %s", ws_url)

    # Set max_size to 4MB to handle large Home Assistant instances
    websocket = await connect(ws_url, max_size=4 * 1024 * 1024)

    # Step 1: Receive auth_required message
    auth_required = await websocket.recv()
    auth_msg = json.loads(auth_required)
    logger.debug("Received auth_required message: %s", auth_msg)

    if auth_msg.get("type") != "auth_required":
        await websocket.close()
        raise RuntimeError(f"Unexpected message: {auth_msg}")

    # Step 2: Send authentication
    auth_payload = {
        "type": "auth",
        "access_token": api_token
    }
    logger.debug("Sending authentication message")
    await websocket.send(json.dumps(auth_payload))

    # Step 3: Receive auth response
    auth_response = await websocket.recv()
    auth_result = json.loads(auth_response)
    logger.debug("Received auth response: %s", auth_result)

    if auth_result.get("type") != "auth_ok":
        logger.error("WebSocket authentication failed: %s", auth_result)
        await websocket.close()
        raise RuntimeError(
            f"Authentication failed: "
            f"{auth_result.get('message', 'Unknown error')}"
        )

    logger.debug("WebSocket authentication successful")

    return websocket, 1  # Return websocket and initial message_id


async def get_entity_state(websocket, entity_id: str, message_id: int):
    """Get the current state and attributes of an entity

    Args:
        websocket: WebSocket connection
        entity_id: Entity ID to fetch
        message_id: Message ID counter

    Returns:
        Tuple of (entity_state_dict, updated_message_id)
    """
    logger.info("Fetching entity state for: %s", entity_id)

    # Request all states
    states_request = {
        "id": message_id,
        "type": "get_states"
    }
    logger.debug("Sending get_states request: %s", states_request)
    await websocket.send(json.dumps(states_request))

    # Receive states response
    states_response = await websocket.recv()
    states_result = json.loads(states_response)
    logger.debug("Received states response: id=%s, type=%s, success=%s",
                 states_result.get("id"), states_result.get("type"),
                 states_result.get("success"))

    if not states_result.get("success"):
        error_msg = states_result.get("error", {}).get("message", "Unknown error")
        logger.error("get_states request failed: %s", error_msg)
        raise RuntimeError(f"Failed to get sensor states: {error_msg}")

    # Find our entity in the results
    states = states_result.get("result", [])
    entity_state = None
    for state in states:
        if state.get("entity_id") == entity_id:
            entity_state = state
            break

    if entity_state is None:
        raise RuntimeError(
            f"Entity '{entity_id}' not found in HomeAssistant. "
            f"Please check the entity_id."
        )

    logger.info("Entity found: %s", json.dumps(entity_state, indent=2))

    return entity_state, message_id + 1


async def get_statistics(websocket, entity_id: str, message_id: int):
    """Get historical statistics for an entity

    Args:
        websocket: WebSocket connection
        entity_id: Entity ID to fetch statistics for
        message_id: Message ID counter

    Returns:
        Tuple of (statistics, updated_message_id)
    """
    logger.info("Fetching statistics for: %s", entity_id)

    # Use a time window to fetch statistics
    # For solar forecast data that's already projected into the future,
    # we need to understand what time period the data covers
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    start_time = now
    end_time = now + datetime.timedelta(hours=96)

    start_iso = start_time.isoformat()
    end_iso = end_time.isoformat()

    logger.info("Fetching statistics from %s to %s", start_iso, end_iso)

    # Request statistics
    stats_request = {
        "id": message_id,
        "type": "recorder/statistics_during_period",
        "start_time": start_iso,
        "end_time": end_iso,
        "statistic_ids": [entity_id],
        "period": "hour"
    }
    logger.debug("Sending statistics request: %s", stats_request)
    await websocket.send(json.dumps(stats_request))

    # Receive statistics response
    stats_response = await websocket.recv()
    stats_result = json.loads(stats_response)
    logger.debug("Received statistics response: id=%s, type=%s, success=%s",
                 stats_result.get("id"), stats_result.get("type"),
                 stats_result.get("success"))

    if not stats_result.get("success"):
        error_msg = stats_result.get("error", {}).get("message", "Unknown error")
        logger.error("Statistics request failed: %s", error_msg)
        raise RuntimeError(f"Statistics request failed: {error_msg}")

    data = stats_result.get("result", {})
    logger.info("Statistics result contains %d entities", len(data))

    if not data or entity_id not in data:
        logger.warning(
            "No statistics data returned for entity %s. "
            "Make sure the entity has long-term statistics enabled.", entity_id)
        return None, message_id + 1

    entity_stats = data[entity_id]
    logger.info("Fetched %d hourly statistics", len(entity_stats))
    logger.info("Statistics data: %s", json.dumps(entity_stats, indent=2))

    return entity_stats, message_id + 1


async def subscribe_to_entity(websocket, entity_id: str, message_id: int):
    """Subscribe to state changes for an entity

    Args:
        websocket: WebSocket connection
        entity_id: Entity ID to subscribe to
        message_id: Message ID counter

    Returns:
        Updated message_id
    """
    logger.info("Subscribing to state changes for: %s", entity_id)

    subscribe_request = {
        "id": message_id,
        "type": "subscribe_entities",
        "entity_ids": [entity_id]
    }
    logger.debug("Sending subscribe_entities request: %s", subscribe_request)
    await websocket.send(json.dumps(subscribe_request))

    # Receive subscription confirmation
    sub_response = await websocket.recv()
    sub_result = json.loads(sub_response)
    logger.debug("Received subscription response: %s", sub_result)

    if sub_result.get("type") == "result":
        logger.info("Subscription confirmed")
        return message_id + 1

    # The next messages will be state change events
    logger.info("Waiting for state change events...")
    for i in range(5):  # Listen for 5 events
        event = await websocket.recv()
        event_data = json.loads(event)
        logger.info("Event %d: %s", i+1, json.dumps(event_data, indent=2))


async def main():
    """Main function to test solar forecast data fetching"""
    logger.info("Starting HomeAssistant solar forecast test")

    try:
        # Connect to HomeAssistant
        websocket, message_id = await websocket_connect(BASE_URL, API_TOKEN)
        logger.info("Connected to HomeAssistant")

        try:
            # Get entity state and attributes
            logger.info("\n=== STEP 1: Fetch Entity State ===")
            entity_state, message_id = await get_entity_state(
                websocket, ENTITY_ID, message_id
            )

            # Extract useful information
            state = entity_state.get("state")
            attributes = entity_state.get("attributes", {})
            logger.info("\nEntity State: %s", state)
            logger.info("Unit of Measurement: %s", attributes.get("unit_of_measurement"))
            logger.info("Icon: %s", attributes.get("icon"))
            logger.info("Friendly Name: %s", attributes.get("friendly_name"))

            # Check for forecast-specific attributes
            logger.info("\nAll attributes:")
            for key, value in attributes.items():
                if isinstance(value, (dict, list)):
                    logger.info("  %s: %s", key, json.dumps(value, indent=4))
                else:
                    logger.info("  %s: %s", key, value)

            # Try to fetch statistics
            logger.info("\n=== STEP 2: Fetch Statistics ===")
            try:
                statistics, message_id = await get_statistics(
                    websocket, ENTITY_ID, message_id
                )
                if statistics:
                    logger.info("\nStatistics Summary:")
                    logger.info("  Total records: %d", len(statistics))
                    if statistics:
                        logger.info("  First record: %s", json.dumps(statistics[0], indent=2))
                        logger.info("  Last record: %s", json.dumps(statistics[-1], indent=2))
            except Exception as e:
                logger.warning("Could not fetch statistics: %s", e)

            logger.info("\n=== TEST COMPLETED ===")

        finally:
            await websocket.close()
            logger.info("WebSocket connection closed")

    except Exception as e:
        logger.error("Error during test: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
