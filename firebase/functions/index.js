const functions = require('firebase-functions');
const admin = require('firebase-admin');
const { PubSub } = require('@google-cloud/pubsub');

admin.initializeApp();
const firestore = admin.firestore();
const pubsub = new PubSub();


exports.notify = functions.firestore
  .document('assets/{uid}')
  .onCreate(async (snapshot, context) => {
    const dataBuffer = Buffer.from(
      JSON.stringify({ document_id: context.params.uid }));
    const topic = pubsub.topic(functions.config().pubsub.topic);

    return topic.publish(dataBuffer);
  });
