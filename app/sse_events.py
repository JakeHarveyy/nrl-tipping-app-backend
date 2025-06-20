# app/sse_events.py
import queue
import json
import time
import logging

log = logging.getLogger(__name__)
event_queue = queue.Queue()

def announce_event(event_type, data):
    log.info(f"Announcing SSE event: Type='{event_type}', Data='{str(data)[:100]}...'")
    event_queue.put({'type': event_type, 'data': data})

def sse_event_stream_generator():
    client_id = str(time.time()) # Simple ID for this connection instance
    log.info(f"SSE Client [{client_id}] connected, starting event stream generator.")
    events_sent_this_connection = 0
    keep_alives_sent = 0
    try:
        while True: # Keep this loop running
            try:
                # Try to get an event, but don't block indefinitely if client might disconnect
                event = event_queue.get(block=False) # Use block=False
                sse_formatted_event = f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                log.info(f"SSE Client [{client_id}]: Sending named event (total for session: {events_sent_this_connection + 1}): {event['type']}")
                log.debug(f"  Data: {str(event['data'])[:100]}...")
                yield sse_formatted_event
                event_queue.task_done()
                events_sent_this_connection += 1
            except queue.Empty:
                # No event in queue, send a keep-alive and sleep briefly
                # This allows the generator to yield control and check for client disconnect
                # log.debug(f"SSE Client [{client_id}]: Queue empty, sending keep-alive #{keep_alives_sent + 1}.")
                yield ": keep-alive\n\n" # Standard SSE comment
                keep_alives_sent += 1
                time.sleep(15) # Sleep for a bit before checking queue again / sending another keep-alive
                              # Adjust sleep duration. Too short = busy loop. Too long = slow keep-alives.
            except Exception as e_inner:
                log.error(f"SSE Client [{client_id}]: Error in inner loop: {e_inner}", exc_info=True)
                # Optionally yield an error event to the client
                yield f"event: stream_error\ndata: {json.dumps({'error': 'Stream error'})}\n\n"
                time.sleep(1)

    except GeneratorExit: # This is raised when the client disconnects
        log.info(f"SSE Client [{client_id}] disconnected by client (GeneratorExit). Sent {events_sent_this_connection} events.")
    except Exception as e_outer:
        log.error(f"SSE Client [{client_id}]: Unhandled outer exception: {e_outer}", exc_info=True)
    finally:
        log.info(f"SSE Client [{client_id}]: Generator exiting. Total events sent this session: {events_sent_this_connection}.")