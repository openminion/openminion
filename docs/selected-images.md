# Selected images and screenshots

Status: development source feature; requires `turns.images.v1` for HTTP clients.

OpenMinion accepts explicitly selected still PNG, JPEG and WebP images. Selecting
or previewing an image does not contact a model. The question and selected images
are sent only when you submit the turn. Literal file paths pasted into chat remain
text; they do not grant file access.

## Choose the image route

A model connection must explicitly declare image support for the exact selected
model. Model names are not capability detection. During `openminion setup`, use
`--supports-images --image-mode native` only for a model whose provider accepts
images. This declares support; it does not perform or certify a live vision test.
Existing credentials remain in the normal profile/connection configuration.

In Focus, configure the existing connection and exact model:

```text
/image mode native CONNECTION MODEL
/image mode separate CONNECTION MODEL describe
/image mode separate CONNECTION MODEL text
/image mode off
```

Native mode sends the question and images to the selected chat model. The native
connection/model must match that chat route. Separate mode uses one logical,
tool-free image reader before the chat turn. The chat model receives the original
question and labeled, untrusted text findings, never image bytes. `describe`
includes visual context; `text` requests visible text and uncertainty only. The
same image-capable model can serve both roles, or a separate reader can accompany
a text-only chat model. There is no automatic provider fallback.

Noninteractive setup also accepts `--image-mode separate`,
`--image-reader-connection`, `--image-reader-model` and `--image-purpose` for an
already configured reader connection. Saving image settings preserves the chat
model unless the ordinary setup operation itself selects a new chat model.

## Select and send

```bash
openminion run --image ./screenshot.png "Explain this screen"
openminion run --image ./screenshot.png
```

Repeat `--image` to attach multiple images. In Focus:

```text
/image add ./screenshot.png
/image
/image remove 1
```

Then send your question, or submit an image-only turn. Image drafts cannot enter
the text-only busy queue. Failed or cancelled Focus turns retain selected paths
for an explicit resend; they are not replayed automatically.

## Limits, history and consent

- At most four still images per turn, 10 MiB per input and 20 MiB combined,
  including normalized-image limits. Decoding is bounded to 20 megapixels.
- Core applies orientation and strips metadata before storing normalized PNGs.
- Opaque image refs belong to a particular session generation. Removing a ref,
  deleting its session or revoking the image prevents later access.
- Native history remains tied to its authorized model destination. Changing to
  an incompatible route fails explicitly; it does not silently drop images.
- Separate-reader findings are retained in history without re-reading the image
  on every follow-up. Image instructions remain untrusted data.
- Reader retries use existing provider limits and are charged to the same turn
  budget. Stop/deadline checks apply before main execution; usage already spent
  remains accounted for. Input-token preflight is an estimate, not a raster-cost
  guarantee.

Desktop consumes this same contract when the runtime advertises support. Older
runtimes continue text chat and disable image sending. Automatic observation,
continuous video and local OCR are not part of this feature.
