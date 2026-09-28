## TEST AGENT PROMPT – IMAGE INTEGRATION RULES ##
- Always use base64-encoded images for all tests and requests.
- Accepted formats: JPEG, PNG, WEBP only.
- Do not upload blank, solid-color, or uniform-variance images.
- Every image must contain real visual features — objects, edges, textures.
- If not PNG/JPEG/WEBP, transcode to PNG or JPEG before upload.
- Re-detect MIME after transformations.
- If animated (GIF/APNG), extract the first frame only.
- Resize large images to reasonable bounds.
