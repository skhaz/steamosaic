const functions = require('firebase-functions');
const admin = require('firebase-admin');
const { PubSub } = require('@google-cloud/pubsub');
const { BigQuery } = require('@google-cloud/bigquery');

admin.initializeApp();
const firestore = admin.firestore();
const pubsub = new PubSub();
const bigquery = new BigQuery();


exports.notify = functions.firestore
  .document('users/{uid}')
  .onCreate((snapshot, context) => {
    const { uid } = context.params;
    const dataBuffer = Buffer.from(JSON.stringify({ uid }));
    const topic = pubsub.topic(functions.config().pubsub.topic);

    return topic.publish(dataBuffer);
  });

exports.ticker = functions.pubsub
  .topic('ticker')
  .onPublish(async (_) => {
    const result = await firestore
      .collection('users')
      .where('error', '>', '')
      .limit(500)
      .get();

    const batch = firestore.batch();

    result.forEach(doc => {
      batch.delete(doc.ref);
    });

    return batch.commit();
  });

exports.analytics = functions.pubsub
  .topic('analytics')
  .onPublish(async (_) => {
    const query = `SELECT COUNT(*) FROM (
      SELECT
        REGEXP_EXTRACT(textPayload, r'user (\\w+)') AS steam_id
      FROM
        \`steamosaic.steam_ids.cloudfunctions_googleapis_com_cloud_functions_*\`
      WHERE
        textPayload LIKE '%fetching steam_id%'
      GROUP BY
        steam_id)`

    const response = await bigquery.query(query);
    const counter = response[0][0].f0_;
    return firestore.doc('stats/users').set({ counter });
  });
