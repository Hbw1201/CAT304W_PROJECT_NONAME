const admin = require('firebase-admin');
const { onCall, HttpsError } = require('firebase-functions/v2/https');
const { onDocumentCreated } = require('firebase-functions/v2/firestore');

if (!admin.apps.length) {
  admin.initializeApp();
}

const db = admin.firestore();
const { FieldValue } = admin.firestore;

function pickDisplayName(data, fallback) {
  if (!data) return fallback;
  return (
    String(data.fullName || '').trim() ||
    String(data.name || '').trim() ||
    String(data.displayName || '').trim() ||
    String(data.email || '').trim() ||
    fallback
  );
}

const MESSAGE_PREVIEW_MAX = 120;

function buildLastMessage(message) {
  const type = String(message?.type || '').trim();
  if (type === 'text') {
    const text = typeof message?.text === 'string' ? message.text : '';
    return text.slice(0, MESSAGE_PREVIEW_MAX);
  }
  if (type === 'file' || type === 'image') {
    return '📎 File';
  }
  if (type === 'report_card') {
    return '🧪 Screening Report';
  }
  if (type === 'appointment_card') {
    return '📅 Appointment';
  }
  return '';
}

function hasOwn(data, key) {
  return Object.prototype.hasOwnProperty.call(data, key);
}

exports.assignDoctorToPatient = onCall(
  { region: 'asia-east2', invoker: 'public' },
  async (request) => {
    const auth = request.auth;
    if (!auth) {
      throw new HttpsError('unauthenticated', 'Login required');
    }

    const patientUid = String(auth.uid || '').trim();
    if (!patientUid) {
      throw new HttpsError('invalid-argument', 'patientUid is required');
    }

    const result = await db.runTransaction(async (tx) => {
      const patientRef = db.collection('users').doc(patientUid);
      const patientSnap = await tx.get(patientRef);
      const patientData = patientSnap.exists ? patientSnap.data() : {};
      const existingDoctorId = String(patientData?.doctorId || '').trim();
      if (existingDoctorId) {
        const existingConversationId = `dp_${patientUid}_${existingDoctorId}`;
        return { doctorId: existingDoctorId, conversationId: existingConversationId };
      }

      const doctorsQuery = db.collection('users').where('role', '==', 'doctor');
      const doctorsSnap = await tx.get(doctorsQuery);
      if (doctorsSnap.empty) {
        throw new HttpsError('failed-precondition', 'No doctors available');
      }

      const doctorDocs = doctorsSnap.docs;
      const pickedIndex =
        doctorDocs.length === 1 ? 0 : Math.floor(Math.random() * doctorDocs.length);
      const doctorSnap = doctorDocs[pickedIndex];
      const doctorId = doctorSnap.id;
      const doctorData = doctorSnap.data() || {};

      const conversationId = `dp_${patientUid}_${doctorId}`;
      const conversationRef = db.collection('conversations').doc(conversationId);
      const doctorPatientId = `${doctorId}_${patientUid}`;
      const doctorPatientRef = db.collection('doctorPatients').doc(doctorPatientId);

      const conversationSnap = await tx.get(conversationRef);
      const doctorPatientSnap = await tx.get(doctorPatientRef);

      const now = FieldValue.serverTimestamp();
      const patientName = pickDisplayName(patientData, 'Patient');
      const doctorName = pickDisplayName(doctorData, 'Doctor');

      tx.set(
        patientRef,
        {
          doctorId,
          assignedDoctorAt: now,
        },
        { merge: true }
      );

      if (!conversationSnap.exists) {
        tx.set(conversationRef, {
          type: 'doctor_patient',
          patientId: patientUid,
          doctorId,
          patientName,
          doctorName,
          lastMessageText: '',
          lastMessageAt: now,
          patientUnread: 0,
          doctorUnread: 0,
          createdAt: now,
        });
      }

      if (!doctorPatientSnap.exists) {
        tx.set(
          doctorPatientRef,
          {
            doctorId,
            patientId: patientUid,
            status: 'active',
            assignedAt: now,
            updatedAt: now,
          },
          { merge: true }
        );
      }

      return { doctorId, conversationId };
    });

    return result;
  }
);

exports.onChatMessageCreated = onDocumentCreated(
  { region: 'asia-east2', document: 'chats/{chatId}/messages/{msgId}' },
  async (event) => {
    const messageSnap = event.data;
    if (!messageSnap) return;

    const message = messageSnap.data() || {};
    const chatId = event.params.chatId;
    if (!chatId) return;

    const senderRole = String(message.senderRole || '').trim();
    const messageType = String(message.type || '').trim();
    const lastMessageAt = message.createdAt || FieldValue.serverTimestamp();

    const updates = {
      lastMessage: buildLastMessage(message),
      lastMessageAt,
      updatedAt: FieldValue.serverTimestamp(),
      lastSenderRole: senderRole || null,
    };

    if (senderRole === 'doctor') {
      updates.unreadCountPatient = FieldValue.increment(1);
    } else if (senderRole === 'patient') {
      updates.unreadCountDoctor = FieldValue.increment(1);
    }

    if (messageType === 'report_card' && hasOwn(message, 'linkedReportId')) {
      updates.linkedReportId = message.linkedReportId ?? null;
    }

    if (
      messageType === 'appointment_card' &&
      hasOwn(message, 'linkedAppointmentId')
    ) {
      updates.linkedAppointmentId = message.linkedAppointmentId ?? null;
    }

    const chatRef = db.collection('chats').doc(chatId);
    await db.runTransaction(async (tx) => {
      const chatSnap = await tx.get(chatRef);
      if (!chatSnap.exists) return;
      tx.update(chatRef, updates);
    });
  }
);
