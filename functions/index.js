const functions = require("firebase-functions");
const admin = require("firebase-admin");
const { PubSub } = require("@google-cloud/pubsub");

admin.initializeApp();
const firestore = admin.firestore();
const pubsub = new PubSub();

exports.notify = functions.firestore
  .document("users/{uid}")
  .onCreate((snapshot, context) => {
    const { uid } = context.params;
    const dataBuffer = Buffer.from(JSON.stringify({ uid }));
    const topic = pubsub.topic(functions.config().pubsub.topic);

    return topic.publish(dataBuffer);
  });

exports.ticker = functions.pubsub
  .schedule("every 5 minutes")
  .onRun(async (context) => {
    const result = await firestore
      .collection("users")
      .where("error", ">", "")
      .limit(500)
      .get();

    const batch = firestore.batch();

    result.forEach((document) => {
      batch.delete(document.ref);
    });

    return batch.commit();
  });
