# Steamosaic: Generate a mosaic of your steam account

![](firebase/public/images/mosaic.jpg)

## Why firebase?

The very first version was using a Google Cloud Function that's takes a user as input and generates the mosaic, then returns the image. On the frontend side, I used `XMLHttpRequest` to request the mosaic, and that's it.

But as you can see many issues can occur, like timeouts, cache misses, waste of resources, etc.

So instead of pull out a over-engineered task queue or something like, I changed the cloud function to subscribe to a pubsub topic and the on frontend side creates or start to listen the firestore document that holds the needed.

On the firebase side, whatever a document is created, a function publishes on the topic that another _worker function_ is listening. 

When the _worker function_ finishes, it write back to the document the public url of the mosaic image triggering the frontend to display it.

![](diagram.png)

If you want to take a look more deeply, these are the main files [main.py], [index.js] and [app.js].

[main.py]: https://github.com/skhaz/steamosaic/blob/master/functions/main.py
[index.js]: https://github.com/skhaz/steamosaic/blob/master/firebase/functions/index.js
[app.js]: https://github.com/skhaz/steamosaic/blob/master/firebase/public/js/app.js
