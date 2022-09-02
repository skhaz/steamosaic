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

    return Promise.all([
      topic.publish(dataBuffer),
      firestore
        .collection("counters")
        .doc("summary")
        .update({ total: admin.firestore.FieldValue.increment(1) }),
    ]);
  });

exports.ticker = functions.pubsub
  .schedule("every 1 minutes")
  .onRun(async (context) => {
    const batch = firestore.batch();

    const q1 = await firestore
      .collection("users")
      .where("error", ">", "")
      .get();

    q1.forEach((document) => {
      batch.delete(document.ref);
    });

    const days = 365 * (86400 * 1000);

    const q2 = await firestore
      .collection("users")
      .where("timestamp", "<", new Date(Date.now() - days))
      .get();

    q2.forEach((document) => {
      batch.delete(document.ref);
    });

    return batch.commit();
  });
