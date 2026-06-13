import { createRoot } from 'react-dom/client'
import App from './App.tsx'
import './index.css'

// --- REACT 19 DOM LIFECYCLE POLYFILL FOR @DND-KIT ---
// Rationale: React 19's asynchronous commit phase and DOM reparenting routinely 
// force the browser to execute releasePointerCapture(), emitting a synthetic 
// 'pointercancel' event. @dnd-kit's PointerSensor listens for this event and 
// will silently abort the drag, swallowing the onDragEnd lifecycle.
// This polyfill intercepts and suppresses synthetic cancellations during active drags.

if (typeof window!== 'undefined') {
  const originalAddEventListener = EventTarget.prototype.addEventListener;
  
  EventTarget.prototype.addEventListener = function (
    type: string,
    listener: EventListenerOrEventListenerObject,
    options?: boolean | AddEventListenerOptions
  ) {
    if (type === 'pointercancel') {
      const wrappedListener = function (this: EventTarget, e: Event) {
        const pointerEvent = e as PointerEvent;
        
        // Detect if @dnd-kit is currently engaged in an active drag operation.
        // During a drag, dnd-kit applies grabbing cursors and specific ARIA attributes
        // to the document body and the active draggable node.
        const isActivelyDragging = 
          document.body.style.cursor === 'grabbing' || 
          document.body.style.cursor === 'grab' ||
          document.querySelector('[aria-roledescription="sortable"]')!== null;

        if (isActivelyDragging) {
          // Suppress the synthetic pointercancel to protect the active drag state
          // from React 19's DOM reconciliation interrupts.
          console.debug("🛡️ Suppressed synthetic pointercancel during active drag.");
          return; 
        }

        // Pass through legitimate events when no drag is active
        if (typeof listener === 'function') {
          return listener.call(this, pointerEvent);
        } else if (listener && typeof listener === 'object') {
          return listener.handleEvent(pointerEvent);
        }
      };
      
      // Attach the wrapped listener instead of the original
      return originalAddEventListener.call(this, type, wrappedListener, options);
    }
    
    // For all other event types, execute the standard DOM behavior
    return originalAddEventListener.call(this, type, listener, options);
  };
}
// ----------------------------------------------------

// Initialize the React 19 Fiber Tree without Strict Mode
createRoot(document.getElementById('root')!).render(
    <App />
)