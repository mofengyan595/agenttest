// Disposable acceptance fixture. No custom routes, hooks, or replacement business handlers.
migrate((app) => {
  const users = new Collection({
    name: "acceptance_users",
    type: "auth",
    listRule: null,
    viewRule: "id = @request.auth.id",
    createRule: null,
    updateRule: null,
    deleteRule: null,
    passwordAuth: { enabled: true, identityFields: ["email"] },
  });
  app.save(users);

  for (const [role, id] of [["OWNER", "owneraccount001"], ["OTHER", "otheraccount001"]]) {
    const email = $os.getenv("PB_" + role + "_EMAIL");
    const password = $os.getenv("PB_" + role + "_PASSWORD");
    if (!email || password.length < 24) throw new Error("ACCEPTANCE_CREDENTIALS_REQUIRED");
    const record = new Record(users);
    record.set("id", id);
    record.set("email", email);
    record.set("password", password);
    record.set("verified", true);
    app.save(record);
  }

  const ownerRule = "@request.auth.id != '' && owner = @request.auth.id";
  app.save(new Collection({
    name: "acceptance_notes",
    type: "base",
    listRule: ownerRule,
    viewRule: ownerRule,
    createRule: ownerRule,
    updateRule: ownerRule + " && @request.body.owner:changed = false",
    deleteRule: ownerRule,
    fields: [
      { name: "title", type: "text", required: true, min: 1, max: 80 },
      { name: "state", type: "select", required: true, maxSelect: 1, values: ["draft", "done"] },
      { name: "owner", type: "relation", required: true, maxSelect: 1, collectionId: users.id },
    ],
  }));

  // Keep the isolated service from persisting API/authentication logs.
  const settings = app.settings();
  settings.logs.maxDays = 0;
  app.save(settings);
});
