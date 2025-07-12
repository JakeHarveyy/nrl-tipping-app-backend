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
        while True: 
            try:
                # Try to get an event, but don't block indefinitely if client might disconnect
                event = event_queue.get(block=False) 
                sse_formatted_event = f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                log.info(f"SSE Client [{client_id}]: Sending named event (total for session: {events_sent_this_connection + 1}): {event['type']}")
                log.debug(f"  Data: {str(event['data'])[:100]}...")
                yield sse_formatted_event
                event_queue.task_done()
                events_sent_this_connection += 1
            except queue.Empty:
                yield ": keep-alive\n\n" 
                keep_alives_sent += 1
                time.sleep(15) 
            except Exception as e_inner:
                log.error(f"SSE Client [{client_id}]: Error in inner loop: {e_inner}", exc_info=True)
                
                yield f"event: stream_error\ndata: {json.dumps({'error': 'Stream error'})}\n\n"
                time.sleep(1)

    except GeneratorExit: #raised when the client disconnects
        log.info(f"SSE Client [{client_id}] disconnected by client (GeneratorExit). Sent {events_sent_this_connection} events.")
    except Exception as e_outer:
        log.error(f"SSE Client [{client_id}]: Unhandled outer exception: {e_outer}", exc_info=True)
    finally:
        log.info(f"SSE Client [{client_id}]: Generator exiting. Total events sent this session: {events_sent_this_connection}.")