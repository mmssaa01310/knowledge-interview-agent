# AI Interviewer Voice

`app/voice` is the realtime voice gateway for the AI interviewer.

Responsibilities:

* WebRTC signaling and session control
* Runtime lifecycle management
* Transcript forwarding to `app/api`
* Assistant reply playback control

Non-responsibilities:

* Interview state transitions
* RAG
* Evaluation logic
* Direct imports from `app/api`
