import os
from datetime import datetime, date
from functools import wraps

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

BASEDIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASEDIR, "instance", "reserva_tablets.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para continuar."
login_manager.login_message_category = "warning"

ROLES = ["chefe_geral", "admin", "professor"]
ROLE_LABELS = {
    "chefe_geral": "Chefe Geral",
    "admin": "Administrador",
    "professor": "Professor",
}

STATUS_LABELS = {
    "pendente": "Pendente",
    "retirado": "Retirado",
    "devolvido": "Devolvido",
    "cancelado": "Cancelado",
}


# ----------------------- MODELS -----------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="professor")
    ativo = db.Column(db.Boolean, default=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    reservas = db.relationship("Reserva", backref="professor", lazy=True)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def check_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)

    @property
    def label_role(self):
        return ROLE_LABELS.get(self.role, self.role)


class Estoque(db.Model):
    __tablename__ = "estoque"
    id = db.Column(db.Integer, primary_key=True)
    total_tablets = db.Column(db.Integer, nullable=False, default=70)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get():
        e = Estoque.query.first()
        if not e:
            e = Estoque(total_tablets=70)
            db.session.add(e)
            db.session.commit()
        return e


class Reserva(db.Model):
    __tablename__ = "reservas"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    turma = db.Column(db.String(50))
    data = db.Column(db.Date, nullable=False)
    horario_retirada = db.Column(db.String(10))
    horario_devolucao = db.Column(db.String(10))
    atividade = db.Column(db.String(255))
    quantidade_solicitada = db.Column(db.Integer, nullable=False, default=1)
    quantidade_devolvida = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default="pendente")  # pendente, retirado, devolvido, cancelado
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    professor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    @property
    def label_status(self):
        return STATUS_LABELS.get(self.status, self.status)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ----------------------- HELPERS -----------------------

def roles_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                flash("Você não tem permissão para acessar essa página.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return wrapped
    return decorator


def tablets_em_uso():
    total = db.session.query(db.func.coalesce(db.func.sum(Reserva.quantidade_solicitada), 0)).filter(
        Reserva.status == "retirado"
    ).scalar()
    return total or 0


def tablets_disponiveis():
    estoque = Estoque.get()
    return estoque.total_tablets - tablets_em_uso()


# ----------------------- AUTH ROUTES -----------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        senha = request.form.get("senha", "")
        user = User.query.filter_by(username=username).first()
        if user and user.ativo and user.check_senha(senha):
            login_user(user)
            flash(f"Bem-vindo, {user.nome}!", "success")
            return redirect(url_for("dashboard"))
        flash("Usuário ou senha inválidos, ou conta inativa.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Você saiu do sistema.", "info")
    return redirect(url_for("login"))


# ----------------------- DASHBOARD -----------------------

@app.route("/")
@login_required
def dashboard():
    estoque = Estoque.get()
    em_uso = tablets_em_uso()
    disponiveis = estoque.total_tablets - em_uso

    if current_user.role == "professor":
        minhas_reservas = Reserva.query.filter_by(professor_id=current_user.id).order_by(Reserva.data.desc()).limit(10).all()
        return render_template(
            "dashboard_professor.html",
            estoque=estoque, em_uso=em_uso, disponiveis=disponiveis,
            minhas_reservas=minhas_reservas
        )
    else:
        hoje = date.today()
        reservas_hoje = Reserva.query.filter_by(data=hoje).order_by(Reserva.horario_retirada).all()
        pendentes = Reserva.query.filter_by(status="pendente").count()
        retirados = Reserva.query.filter_by(status="retirado").count()
        total_usuarios = User.query.count()
        return render_template(
            "dashboard_admin.html",
            estoque=estoque, em_uso=em_uso, disponiveis=disponiveis,
            reservas_hoje=reservas_hoje, pendentes=pendentes, retirados=retirados,
            total_usuarios=total_usuarios
        )


# ----------------------- RESERVAS -----------------------

@app.route("/reservas")
@login_required
def listar_reservas():
    query = Reserva.query
    if current_user.role == "professor":
        query = query.filter_by(professor_id=current_user.id)

    status_filtro = request.args.get("status", "")
    busca_nome   = request.args.get("busca_nome", "").strip()
    busca_turma  = request.args.get("busca_turma", "").strip()
    busca_data   = request.args.get("busca_data", "").strip()

    if status_filtro:
        query = query.filter_by(status=status_filtro)
    if busca_nome:
        query = query.filter(Reserva.nome.ilike(f"%{busca_nome}%"))
    if busca_turma:
        query = query.filter(Reserva.turma.ilike(f"%{busca_turma}%"))
    if busca_data:
        try:
            data_obj = datetime.strptime(busca_data, "%Y-%m-%d").date()
            query = query.filter(Reserva.data == data_obj)
        except ValueError:
            pass

    reservas = query.order_by(Reserva.data.desc(), Reserva.horario_retirada.desc()).all()
    return render_template(
        "reservas_lista.html",
        reservas=reservas,
        status_filtro=status_filtro,
        busca_nome=busca_nome,
        busca_turma=busca_turma,
        busca_data=busca_data,
    )


@app.route("/reservas/nova", methods=["GET", "POST"])
@login_required
def nova_reserva():
    disponiveis = tablets_disponiveis()
    if request.method == "POST":
        try:
            quantidade = int(request.form.get("quantidade_solicitada", 0))
        except ValueError:
            quantidade = 0

        if quantidade <= 0:
            flash("Informe uma quantidade válida de tablets.", "danger")
            return render_template("reserva_form.html", disponiveis=disponiveis, form=request.form)

        if quantidade > disponiveis:
            flash(f"Quantidade solicitada ({quantidade}) maior que a disponibilidade atual ({disponiveis}).", "danger")
            return render_template("reserva_form.html", disponiveis=disponiveis, form=request.form)

        try:
            data_reserva = datetime.strptime(request.form.get("data"), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            flash("Data inválida.", "danger")
            return render_template("reserva_form.html", disponiveis=disponiveis, form=request.form)

        reserva = Reserva(
            nome=request.form.get("nome") or current_user.nome,
            turma=request.form.get("turma"),
            data=data_reserva,
            horario_retirada=request.form.get("horario_retirada"),
            horario_devolucao=request.form.get("horario_devolucao"),
            atividade=request.form.get("atividade"),
            quantidade_solicitada=quantidade,
            status="pendente",
            professor_id=current_user.id,
        )
        db.session.add(reserva)
        db.session.commit()
        flash("Reserva criada com sucesso!", "success")
        return redirect(url_for("listar_reservas"))

    return render_template("reserva_form.html", disponiveis=disponiveis, form={})


@app.route("/reservas/<int:reserva_id>/retirar", methods=["POST"])
@login_required
def retirar_tablets(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)
    if current_user.role == "professor" and reserva.professor_id != current_user.id:
        flash("Você só pode atualizar suas próprias reservas.", "danger")
        return redirect(url_for("listar_reservas"))

    if reserva.status != "pendente":
        flash("Esta reserva já foi processada.", "warning")
        return redirect(url_for("listar_reservas"))

    if reserva.quantidade_solicitada > tablets_disponiveis():
        flash("Não há tablets suficientes disponíveis no estoque para esta retirada.", "danger")
        return redirect(url_for("listar_reservas"))

    reserva.status = "retirado"
    db.session.commit()
    flash("Retirada registrada.", "success")
    return redirect(url_for("listar_reservas"))


@app.route("/reservas/<int:reserva_id>/devolver", methods=["POST"])
@login_required
def devolver_tablets(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)
    if current_user.role == "professor" and reserva.professor_id != current_user.id:
        flash("Você só pode atualizar suas próprias reservas.", "danger")
        return redirect(url_for("listar_reservas"))

    if reserva.status != "retirado":
        flash("Esta reserva não está com status 'retirado'.", "warning")
        return redirect(url_for("listar_reservas"))

    try:
        qtd_devolvida = int(request.form.get("quantidade_devolvida", reserva.quantidade_solicitada))
    except ValueError:
        qtd_devolvida = reserva.quantidade_solicitada

    reserva.quantidade_devolvida = qtd_devolvida
    reserva.status = "devolvido"
    db.session.commit()
    flash("Devolução registrada.", "success")
    return redirect(url_for("listar_reservas"))


@app.route("/reservas/<int:reserva_id>/cancelar", methods=["POST"])
@login_required
def cancelar_reserva(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)
    if current_user.role == "professor" and reserva.professor_id != current_user.id:
        flash("Você só pode cancelar suas próprias reservas.", "danger")
        return redirect(url_for("listar_reservas"))
    if reserva.status not in ("pendente", "retirado"):
        flash("Esta reserva não pode ser cancelada.", "warning")
        return redirect(url_for("listar_reservas"))
    reserva.status = "cancelado"
    db.session.commit()
    flash("Reserva cancelada.", "info")
    return redirect(url_for("listar_reservas"))


@app.route("/reservas/<int:reserva_id>/excluir", methods=["POST"])
@login_required
@roles_required("chefe_geral", "admin")
def excluir_reserva(reserva_id):
    reserva = Reserva.query.get_or_404(reserva_id)
    db.session.delete(reserva)
    db.session.commit()
    flash("Reserva excluída.", "info")
    return redirect(url_for("listar_reservas"))


# ----------------------- ESTOQUE -----------------------

@app.route("/estoque", methods=["GET", "POST"])
@login_required
@roles_required("chefe_geral", "admin")
def estoque_view():
    estoque = Estoque.get()
    if request.method == "POST":
        try:
            novo_total = int(request.form.get("total_tablets"))
            if novo_total < 0:
                raise ValueError
            estoque.total_tablets = novo_total
            db.session.commit()
            flash("Estoque atualizado com sucesso.", "success")
        except (ValueError, TypeError):
            flash("Informe um número válido para o total de tablets.", "danger")
        return redirect(url_for("estoque_view"))

    em_uso = tablets_em_uso()
    disponiveis = estoque.total_tablets - em_uso
    return render_template("estoque.html", estoque=estoque, em_uso=em_uso, disponiveis=disponiveis)


# ----------------------- USUÁRIOS -----------------------

@app.route("/usuarios")
@login_required
@roles_required("chefe_geral", "admin")
def listar_usuarios():
    usuarios = User.query.order_by(User.nome).all()
    return render_template("usuarios_lista.html", usuarios=usuarios)


@app.route("/usuarios/novo", methods=["GET", "POST"])
@login_required
@roles_required("chefe_geral", "admin")
def novo_usuario():
    # admin não pode criar outro chefe_geral
    roles_permitidos = ROLES if current_user.role == "chefe_geral" else ["admin", "professor"]

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        username = request.form.get("username", "").strip()
        senha = request.form.get("senha", "")
        role = request.form.get("role")

        if role not in roles_permitidos:
            flash("Você não tem permissão para criar esse tipo de usuário.", "danger")
            return render_template("usuario_form.html", roles=roles_permitidos, role_labels=ROLE_LABELS, form=request.form)

        if not nome or not username or not senha:
            flash("Preencha todos os campos obrigatórios.", "danger")
            return render_template("usuario_form.html", roles=roles_permitidos, role_labels=ROLE_LABELS, form=request.form)

        if User.query.filter_by(username=username).first():
            flash("Já existe um usuário com esse nome de usuário.", "danger")
            return render_template("usuario_form.html", roles=roles_permitidos, role_labels=ROLE_LABELS, form=request.form)

        usuario = User(nome=nome, username=username, role=role, ativo=True)
        usuario.set_senha(senha)
        db.session.add(usuario)
        db.session.commit()
        flash(f"Usuário {nome} criado com sucesso.", "success")
        return redirect(url_for("listar_usuarios"))

    return render_template("usuario_form.html", roles=roles_permitidos, role_labels=ROLE_LABELS, form={})


@app.route("/usuarios/<int:user_id>/editar", methods=["GET", "POST"])
@login_required
@roles_required("chefe_geral", "admin")
def editar_usuario(user_id):
    usuario = User.query.get_or_404(user_id)

    # admin não pode editar um chefe_geral
    if usuario.role == "chefe_geral" and current_user.role != "chefe_geral":
        flash("Você não tem permissão para editar este usuário.", "danger")
        return redirect(url_for("listar_usuarios"))

    roles_permitidos = ROLES if current_user.role == "chefe_geral" else ["admin", "professor"]

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        username = request.form.get("username", "").strip()
        role = request.form.get("role")
        nova_senha = request.form.get("nova_senha", "").strip()

        if role not in roles_permitidos:
            flash("Você não tem permissão para definir esse perfil.", "danger")
            return render_template("usuario_editar.html", usuario=usuario, roles=roles_permitidos, role_labels=ROLE_LABELS)

        if not nome or not username:
            flash("Nome e usuário são obrigatórios.", "danger")
            return render_template("usuario_editar.html", usuario=usuario, roles=roles_permitidos, role_labels=ROLE_LABELS)

        conflito = User.query.filter(User.username == username, User.id != user_id).first()
        if conflito:
            flash("Já existe outro usuário com esse nome de usuário.", "danger")
            return render_template("usuario_editar.html", usuario=usuario, roles=roles_permitidos, role_labels=ROLE_LABELS)

        usuario.nome = nome
        usuario.username = username
        usuario.role = role
        if nova_senha:
            usuario.set_senha(nova_senha)
        db.session.commit()
        flash(f"Usuário {nome} atualizado com sucesso.", "success")
        return redirect(url_for("listar_usuarios"))

    return render_template("usuario_editar.html", usuario=usuario, roles=roles_permitidos, role_labels=ROLE_LABELS)


@app.route("/usuarios/<int:user_id>/ativar", methods=["POST"])
@login_required
@roles_required("chefe_geral", "admin")
def alternar_usuario(user_id):
    usuario = User.query.get_or_404(user_id)
    if usuario.role == "chefe_geral" and current_user.role != "chefe_geral":
        flash("Você não tem permissão para alterar este usuário.", "danger")
        return redirect(url_for("listar_usuarios"))
    if usuario.id == current_user.id:
        flash("Você não pode desativar a si mesmo.", "warning")
        return redirect(url_for("listar_usuarios"))
    usuario.ativo = not usuario.ativo
    db.session.commit()
    flash("Status do usuário atualizado.", "info")
    return redirect(url_for("listar_usuarios"))


@app.route("/usuarios/<int:user_id>/excluir", methods=["POST"])
@login_required
@roles_required("chefe_geral")
def excluir_usuario(user_id):
    usuario = User.query.get_or_404(user_id)
    if usuario.id == current_user.id:
        flash("Você não pode excluir a si mesmo.", "danger")
        return redirect(url_for("listar_usuarios"))
    db.session.delete(usuario)
    db.session.commit()
    flash("Usuário excluído com sucesso.", "info")
    return redirect(url_for("listar_usuarios"))


# ----------------------- API AUXILIAR -----------------------

@app.route("/api/disponibilidade")
@login_required
def api_disponibilidade():
    return jsonify({"disponiveis": tablets_disponiveis(), "total": Estoque.get().total_tablets, "em_uso": tablets_em_uso()})


# ----------------------- CLI / INIT -----------------------

@app.cli.command("init-db")
def init_db_command():
    """Cria as tabelas e um usuário chefe_geral padrão."""
    db.create_all()
    Estoque.get()
    if not User.query.filter_by(username="chefe").first():
        chefe = User(nome="Chefe Geral", username="chefe", role="chefe_geral", ativo=True)
        chefe.set_senha("chefe123")
        db.session.add(chefe)
        db.session.commit()
        print("Usuário chefe_geral criado -> usuário: chefe / senha: chefe123")
    print("Banco de dados inicializado.")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        Estoque.get()
        if not User.query.filter_by(username="chefe").first():
            chefe = User(nome="Chefe Geral", username="chefe", role="chefe_geral", ativo=True)
            chefe.set_senha("chefe123")
            db.session.add(chefe)
            db.session.commit()
            print("Usuário chefe_geral criado -> usuário: chefe / senha: chefe123")
    app.run(debug=True)
