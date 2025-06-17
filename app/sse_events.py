# app/sse_events.py
import queue
import json
import time
import logging

log = logging.getLogger(__name__)

# A simple in-memory queue to hold events to be broadcasted
# For production with multiple workers, this needs to be replaced with
# a proper message queue (Redis Pub/Sub, RabbitMQ, Kafka, etc.)
event_queue = queue.Queue()

def announce_event(event_type, data):
    """Puts an event onto the queue."""
    log.info(f"Announcing SSE event: Type='{event_type}', Data='{str(data)[:100]}...'")
    event_queue.put({'type': event_type, 'data': data})

def sse_event_stream_generator():
    """
    A generator function for the SSE stream.
    Yields events from the queue.
    This will be run in a separate thread/greenlet per client connection by Flask/WSGI server.
    """
    # For a very simple demo, this keeps a client listening.
    # In a real app, you might have client-specific queues or filtering.
    # This simple version broadcasts ALL events to ALL connected SSE clients.
    log.info("SSE client connected, starting event stream generator.")
    try:
        while True:
            # Try to get an event from the queue.
            # This blocks until an item is available or timeout occurs (if timeout specified)
            # For non-blocking, use get_nowait() and handle queue.Empty, then sleep.
            try:
                event = event_queue.get(timeout=60) # Timeout to allow checking connection
                # Format for SSE:
                # event: event_name (optional)
                # data: json_payload
                # id: some_id (optional)
                # retry: milliseconds (optional)
                sse_formatted_event = f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                log.debug(f"SSE Generator: Sending event: {sse_formatted_event.strip()}")
                yield sse_formatted_event
                event_queue.task_done() # Signal that the item has been processed
            except queue.Empty:
                # Timeout occurred, send a comment to keep connection alive if needed,
                # or just continue to allow the loop to check for new messages.
                # log.debug("SSE Generator: Queue empty, sending keep-alive comment.")
                yield ": keep-alive\n\n" # SSE comment to keep connection open
            except Exception as e:
                log.error(f"SSE Generator: Error yielding event: {e}", exc_info=True)
                # Potentially break or handle error differently
                time.sleep(1) # Avoid tight loop on error

    except GeneratorExit:
        # This happens when the client disconnects
        log.info("SSE client disconnected, stopping event stream generator.")
    except Exception as e:
        log.error(f"SSE Generator: Unhandled exception: {e}", exc_info=True)
    finally:
        log.info("SSE Generator: Exiting.")