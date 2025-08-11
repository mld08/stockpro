from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'votre-clé-secrète-ici'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql://myuser:mypassword@localhost/stockdb'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # type: ignore

# Modèles de base de données
class Categorie(db.Model):
    __tablename__ = 'categories'
    id_categorie = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text)
    
    produits = db.relationship('Produit', backref='categorie', lazy=True)

class Fournisseur(db.Model):
    __tablename__ = 'fournisseurs'
    id_fournisseur = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    contact = db.Column(db.String(100))
    adresse = db.Column(db.String(150))
    ville = db.Column(db.String(50))
    pays = db.Column(db.String(50))
    
    produits = db.relationship('Produit', backref='fournisseur', lazy=True)

class Produit(db.Model):
    __tablename__ = 'produits'
    id_produit = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, unique=True)
    nom = db.Column(db.String(100), nullable=False)
    categorie_id = db.Column(db.Integer, db.ForeignKey('categories.id_categorie'))
    unite = db.Column(db.String(20))
    prix_unitaire = db.Column(db.Numeric(10, 2), default=0.00)
    seuil_min = db.Column(db.Integer, default=0)
    stock_actuel = db.Column(db.Integer, default=0)
    id_fournisseur = db.Column(db.Integer, db.ForeignKey('fournisseurs.id_fournisseur'))
    
    mouvements = db.relationship('Mouvement', backref='produit', lazy=True)
    alertes = db.relationship('Alerte', backref='produit', lazy=True)
    
    @property
    def has_alert(self):
        return self.stock_actuel <= self.seuil_min

class Mouvement(db.Model):
    __tablename__ = 'mouvements'
    id_mouvement = db.Column(db.Integer, primary_key=True)
    id_produit = db.Column(db.Integer, db.ForeignKey('produits.id_produit'), nullable=False)
    type_mouvement = db.Column(db.Enum('ENTREE', 'SORTIE', name='type_mouvement'))
    quantite = db.Column(db.Integer, nullable=False)
    date_mouvement = db.Column(db.DateTime, default=datetime.utcnow)
    motif = db.Column(db.Text)
    reference_doc = db.Column(db.String(50))

class Utilisateur(UserMixin, db.Model):
    __tablename__ = 'utilisateurs'
    id_utilisateur = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    login = db.Column(db.String(50), unique=True, nullable=False)
    mot_de_passe = db.Column(db.String(255), nullable=False)
    role = db.Column(db.Enum('ADMIN', 'USER', name='role'), default='USER')
    
    def get_id(self):
        return str(self.id_utilisateur)

class Alerte(db.Model):
    __tablename__ = 'alertes'
    id_alerte = db.Column(db.Integer, primary_key=True)
    id_produit = db.Column(db.Integer, db.ForeignKey('produits.id_produit'), nullable=False)
    date_alerte = db.Column(db.DateTime, default=datetime.utcnow)
    message = db.Column(db.Text, nullable=False)
    statut = db.Column(db.Enum('NOUVELLE', 'TRAITEE', name='statut'), default='NOUVELLE')

@login_manager.user_loader
def load_user(user_id):
    return Utilisateur.query.get(int(user_id))

# Routes principales
@app.route('/')
@login_required
def dashboard():
    # Statistiques pour le dashboard
    total_produits = Produit.query.count()
    produits_critique = Produit.query.filter(Produit.stock_actuel <= Produit.seuil_min).count()
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    alertes_trait = Alerte.query.filter_by(statut='TRAITEE').count()
    
    # Produits récents avec alertes
    produits_alertes = Produit.query.filter(Produit.stock_actuel <= Produit.seuil_min).limit(5).all()
    
    # Mouvements récents
    mouvements_recents = db.session.query(Mouvement, Produit).join(Produit).order_by(Mouvement.date_mouvement.desc()).limit(5).all()

    # Calculer la valeur totale du stock actuelle
    stock_total = db.session.query(func.sum(Produit.stock_actuel * Produit.prix_unitaire)).scalar() or 0
    stock_total = round(stock_total, 0)

    # Calculer la valeur du stock au début du mois
    debut_mois = datetime(datetime.now().year, datetime.now().month, 1)
    produits = Produit.query.all()
    stock_debut_mois = 0
    for produit in produits:
        # Stock actuel
        stock_actuel = produit.stock_actuel
        # Mouvements du mois
        entrees = db.session.query(func.sum(Mouvement.quantite)).filter(
            Mouvement.id_produit == produit.id_produit,
            Mouvement.type_mouvement == 'ENTREE',
            Mouvement.date_mouvement >= debut_mois
        ).scalar() or 0
        sorties = db.session.query(func.sum(Mouvement.quantite)).filter(
            Mouvement.id_produit == produit.id_produit,
            Mouvement.type_mouvement == 'SORTIE',
            Mouvement.date_mouvement >= debut_mois
        ).scalar() or 0
        # Stock au début du mois = stock actuel - entrées du mois + sorties du mois
        stock_debut = stock_actuel - entrees + sorties
        stock_debut_mois += float(stock_debut) * float(produit.prix_unitaire)

    # Calcul du pourcentage d'évolution avec contrôle
    pourcentage = None
    if stock_debut_mois > 0:
        pourcentage = ((float(stock_total) - stock_debut_mois) / stock_debut_mois) * 100
        # Limiter l'affichage à -99.9% et +999.9%
        if pourcentage < -99.9:
            pourcentage = -99.9
        elif pourcentage > 999.9:
            pourcentage = 999.9
        pourcentage = round(pourcentage, 1)

    return render_template('dashboard.html', 
                         total_produits=total_produits,
                         produits_critique=produits_critique,
                         alertes_nouvelles=alertes_nouvelles,
                         alertes_trait=alertes_trait,
                         produits_alertes=produits_alertes,
                         mouvements_recents=mouvements_recents,
                         stock_total=stock_total,
                         pourcentage_stock=pourcentage)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login = request.form['login']
        password = request.form['password']
        
        user = Utilisateur.query.filter_by(login=login).first()
        
        if user and check_password_hash(user.mot_de_passe, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Login ou mot de passe incorrect', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/produits')
@login_required
def produits():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    
    query = Produit.query
    if search:
        query = query.filter(Produit.nom.contains(search) | Produit.code.contains(search))
    
    produits = query.paginate(page=page, per_page=20, error_out=False)
    categories = Categorie.query.all()
    fournisseurs = Fournisseur.query.all()
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    
    return render_template('produit/produits.html', 
                         produits=produits, 
                         categories=categories,
                         fournisseurs=fournisseurs,
                         search=search,
                         alertes_nouvelles=alertes_nouvelles)

@app.route('/produit/nouveau', methods=['GET', 'POST'])
@login_required
def nouveau_produit():
    if request.method == 'POST':
        produit = Produit(
            code=request.form['code'],
            nom=request.form['nom'],
            categorie_id=request.form.get('categorie_id') or None,
            unite=request.form['unite'],
            prix_unitaire=float(request.form['prix_unitaire']),
            seuil_min=int(request.form['seuil_min']),
            stock_actuel=int(request.form['stock_actuel']),
            id_fournisseur=request.form.get('id_fournisseur') or None
        )
        
        try:
            db.session.add(produit)
            db.session.commit()
            flash('Produit ajouté avec succès!', 'success')
            return redirect(url_for('produits'))
        except Exception as e:
            db.session.rollback()
            flash('Erreur lors de l\'ajout du produit', 'error')
    
    categories = Categorie.query.all()
    fournisseurs = Fournisseur.query.all()
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('produit/nouveau_produit.html', categories=categories, fournisseurs=fournisseurs, alertes_nouvelles=alertes_nouvelles)

@app.route('/produit/<int:id_produit>/modifier', methods=['GET', 'POST'])
@login_required
def modifier_produit(id_produit):
    produit = Produit.query.get_or_404(id_produit)
    if request.method == 'POST':
        produit.code = request.form['code']
        produit.nom = request.form['nom']
        produit.categorie_id = request.form.get('categorie_id') or None
        produit.unite = request.form['unite']
        produit.prix_unitaire = float(request.form['prix_unitaire'])
        produit.seuil_min = int(request.form['seuil_min'])
        produit.stock_actuel = int(request.form['stock_actuel'])
        produit.id_fournisseur = request.form.get('id_fournisseur') or None
        try:
            db.session.commit()
            flash('Produit modifié avec succès!', 'success')
            return redirect(url_for('produits'))
        except Exception as e:
            db.session.rollback()
            flash('Erreur lors de la modification du produit', 'error')
    categories = Categorie.query.all()
    fournisseurs = Fournisseur.query.all()
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('produit/modifier_produit.html', produit=produit, categories=categories, fournisseurs=fournisseurs, alertes_nouvelles=alertes_nouvelles)

@app.route('/produit/<int:id_produit>/supprimer', methods=['POST'])
@login_required
def supprimer_produit(id_produit):
    produit = Produit.query.get_or_404(id_produit)
    try:
        db.session.delete(produit)
        db.session.commit()
        flash('Produit supprimé avec succès!', 'success')
        return redirect(url_for('produits'))
    except Exception as e:
        db.session.rollback()
        flash('Erreur lors de la suppression du produit', 'error')
        return redirect(url_for('produits'))

@app.route('/categories')
@login_required
def categories():
    page = request.args.get('page', 1, type=int)
    categories = Categorie.query.paginate(page=page, per_page=20, error_out=False)
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('categorie/categories.html', categories=categories, alertes_nouvelles=alertes_nouvelles)

@app.route('/categorie/nouvelle', methods=['GET', 'POST'])
@login_required
def nouvelle_categorie():
    if request.method == 'POST':
        categorie = Categorie(
            nom=request.form['nom'],
            description=request.form.get('description', '')
        )
        try:
            db.session.add(categorie)
            db.session.commit()
            flash('Catégorie ajoutée avec succès!', 'success')
            return redirect(url_for('categories'))
        except Exception as e:
            db.session.rollback()
            flash('Erreur lors de l\'ajout de la catégorie', 'error')
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('categorie/nouvelle_categorie.html', alertes_nouvelles=alertes_nouvelles)

@app.route('/categorie/<int:id_categorie>/modifier', methods=['GET', 'POST'])
@login_required
def modifier_categorie(id_categorie):
    categorie = Categorie.query.get_or_404(id_categorie)
    if request.method == 'POST':
        categorie.nom = request.form['nom']
        categorie.description = request.form.get('description', '')
        try:
            db.session.commit()
            flash('Catégorie modifiée avec succès!', 'success')
            return redirect(url_for('categories'))
        except Exception as e:
            db.session.rollback()
            flash('Erreur lors de la modification de la catégorie', 'error')

    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('categorie/modifier_categorie.html', categorie=categorie, alertes_nouvelles=alertes_nouvelles)

@app.route('/categorie/<int:id_categorie>/supprimer', methods=['POST'])
@login_required
def supprimer_categorie(id_categorie):
    categorie = Categorie.query.get_or_404(id_categorie)
    try:
        db.session.delete(categorie)
        db.session.commit()
        flash('Catégorie supprimée avec succès!', 'success')
        return redirect(url_for('categories'))
    except Exception as e:
        db.session.rollback()
        flash('Erreur lors de la suppression de la catégorie', 'error')
        return redirect(url_for('categories'))
    
@app.route('/fournisseurs')
@login_required
def fournisseurs():
    page = request.args.get('page', 1, type=int)
    fournisseurs = Fournisseur.query.paginate(page=page, per_page=20, error_out=False)
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('fournisseur/fournisseurs.html', fournisseurs=fournisseurs, alertes_nouvelles=alertes_nouvelles)

@app.route('/fournisseur/nouveau', methods=['GET', 'POST'])
@login_required
def nouveau_fournisseur():
    if request.method == 'POST':
        fournisseur = Fournisseur(
            nom=request.form['nom'],
            contact=request.form.get('contact', ''),
            adresse=request.form.get('adresse', ''),
            ville=request.form.get('ville', ''),
            pays=request.form.get('pays', '')
        )
        try:
            db.session.add(fournisseur)
            db.session.commit()
            flash('Fournisseur ajouté avec succès!', 'success')
            return redirect(url_for('fournisseurs'))
        except Exception as e:
            db.session.rollback()
            flash('Erreur lors de l\'ajout du fournisseur', 'error')
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('fournisseur/nouveau_fournisseur.html', alertes_nouvelles=alertes_nouvelles)

@app.route('/fournisseur/<int:id_fournisseur>/modifier', methods=['GET', 'POST'])
@login_required
def modifier_fournisseur(id_fournisseur):
    fournisseur = Fournisseur.query.get_or_404(id_fournisseur)
    if request.method == 'POST':
        fournisseur.nom = request.form['nom']
        fournisseur.contact = request.form.get('contact', '')
        fournisseur.adresse = request.form.get('adresse', '')
        fournisseur.ville = request.form.get('ville', '')
        fournisseur.pays = request.form.get('pays', '')
        try:
            db.session.commit()
            flash('Fournisseur modifié avec succès!', 'success')
            return redirect(url_for('fournisseurs'))
        except Exception as e:
            db.session.rollback()
            flash('Erreur lors de la modification du fournisseur', 'error')
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('fournisseur/modifier_fournisseur.html', fournisseur=fournisseur, alertes_nouvelles=alertes_nouvelles)

@app.route('/fournisseur/<int:id_fournisseur>/supprimer', methods=['POST'])
@login_required
def supprimer_fournisseur(id_fournisseur):
    fournisseur = Fournisseur.query.get_or_404(id_fournisseur)
    try:
        db.session.delete(fournisseur)
        db.session.commit()
        flash('Fournisseur supprimé avec succès!', 'success')
        return redirect(url_for('fournisseurs'))
    except Exception as e:
        db.session.rollback()
        flash('Erreur lors de la suppression du fournisseur', 'error')
        return redirect(url_for('fournisseurs'))
    

@app.route('/mouvements')
@login_required
def mouvements():
    page = request.args.get('page', 1, type=int)
    mouvements = db.session.query(Mouvement, Produit).join(Produit).order_by(Mouvement.date_mouvement.desc()).paginate(page=page, per_page=20, error_out=False)
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('mouvement/mouvements.html', mouvements=mouvements, alertes_nouvelles=alertes_nouvelles)

@app.route('/mouvement/nouveau', methods=['GET', 'POST'])
@login_required
def nouveau_mouvement():
    if request.method == 'POST':
        produit = Produit.query.get(request.form['id_produit'])
        type_mouvement = request.form['type_mouvement']
        quantite = int(request.form['quantite'])
        
        # Créer le mouvement
        mouvement = Mouvement(
            id_produit=produit.id_produit,
            type_mouvement=type_mouvement,
            quantite=quantite,
            motif=request.form['motif'],
            reference_doc=request.form.get('reference_doc', '')
        )
        
        # Mettre à jour le stock
        if type_mouvement == 'ENTREE':
            produit.stock_actuel += quantite
        else:
            produit.stock_actuel -= quantite
        
        try:
            db.session.add(mouvement)
            db.session.commit()
            
            # Vérifier les alertes
            if produit.has_alert:
                alerte = Alerte(
                    id_produit=produit.id_produit,
                    message=f"Stock critique pour {produit.nom}: {produit.stock_actuel} unités restantes (seuil: {produit.seuil_min})"
                )
                db.session.add(alerte)
                db.session.commit()
                flash(f"Stock critique pour {produit.nom}: {produit.stock_actuel} unités restantes (seuil: {produit.seuil_min})", 'warning')
            else:
                # Si le stock est au-dessus du seuil, modifier l'alerte à traiter
                alerte = Alerte.query.filter_by(id_produit=produit.id_produit, statut='NOUVELLE').first()
                if alerte:
                    alerte.statut = 'TRAITEE'
                    alerte.message = f"Stock normal pour {produit.nom}: {produit.stock_actuel} unités restantes"
                    db.session.add(alerte)
                    db.session.commit()
                flash('Mouvement enregistré avec succès et le stock est normal!', 'success')
            
            #flash('Mouvement enregistré avec succès!', 'success')
            return redirect(url_for('mouvements'))
        except Exception as e:
            db.session.rollback()
            flash('Erreur lors de l\'enregistrement', 'error')
    
    produits = Produit.query.all()
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('mouvement/nouveau_mouvement.html', produits=produits, alertes_nouvelles=alertes_nouvelles)

# @app.route('/mouvement/<int:id_mouvement>/modifier', methods=['GET', 'POST'])
# @login_required
# def modifier_mouvement(id_mouvement):
#     mouvement = db.session.query(Mouvement, Produit).join(Produit).filter(Mouvement.id_mouvement == id_mouvement).first()
#     if not mouvement:
#         flash('Mouvement non trouvé', 'error')
#         return redirect(url_for('mouvements'))
#     mouvement, produit = mouvement
    
#     if request.method == 'POST':
#         type_mouvement = request.form['type_mouvement']
#         quantite = int(request.form['quantite'])
#         ancien_quantite = mouvement.quantite
        
#         # Mettre à jour le mouvement
#         mouvement.type_mouvement = type_mouvement
#         mouvement.quantite = quantite
#         mouvement.motif = request.form['motif']
#         mouvement.reference_doc = request.form.get('reference_doc', '')
#         # Mettre à jour le stock du produit
#         if type_mouvement == 'ENTREE':
#             produit.stock_actuel += quantite - ancien_quantite
#         else:
#             produit.stock_actuel -= ancien_quantite - quantite

#         # Vérifier les alertes
#         if produit.has_alert:
#             alerte = Alerte(
#                 id_produit=produit.id_produit,
#                 message=f"Stock critique pour {produit.nom}: {produit.stock_actuel} unités restantes (seuil: {produit.seuil_min})"
#             )
#             db.session.add(alerte)
#             db.session.commit()
#             flash(f"Stock critique pour {produit.nom}: {produit.stock_actuel} unités restantes (seuil: {produit.seuil_min})", 'warning')
#         else:
#             # Si le stock est au-dessus du seuil, modifier l'alerte à traiter
#             alerte = Alerte.query.filter_by(id_produit=produit.id_produit, statut='NOUVELLE').first()
#             if alerte:
#                 alerte.statut = 'TRAITEE'
#                 alerte.message = f"Stock normal pour {produit.nom}: {produit.stock_actuel} unités restantes"
#                 db.session.add(alerte)
#                 db.session.commit()
            
#         try:
#             db.session.commit()
#             flash('Mouvement modifié avec succès!', 'success')
#             return redirect(url_for('mouvements'))
#         except Exception as e:
#             db.session.rollback()
#             flash('Erreur lors de la modification du mouvement', 'error')
#     produits = Produit.query.all()
#     return render_template('mouvement/modifier_mouvement.html', mouvement=mouvement, produit=produit, produits=produits)

# @app.route('/mouvement/<int:id_mouvement>/supprimer', methods=['POST'])
# @login_required
# def supprimer_mouvement(id_mouvement):
#     mouvement = Mouvement.query.get_or_404(id_mouvement)
#     produit = Produit.query.get(mouvement.id_produit)
#     try:
#         # Mettre à jour le stock du produit
#         if mouvement.type_mouvement == 'ENTREE':
#             produit.stock_actuel -= mouvement.quantite
#         else:
#             produit.stock_actuel += mouvement.quantite
#         if not produit.has_alert:
#             # Si le stock est au-dessus du seuil, modifier l'alerte à traiter
#             alerte = Alerte.query.filter_by(id_produit=produit.id_produit, statut='NOUVELLE').first()
#             if alerte:
#                 db.session.delete(alerte)
#                 db.session.commit()
#         db.session.delete(mouvement)
#         db.session.commit()
#         flash('Mouvement supprimé avec succès!', 'success')
#         return redirect(url_for('mouvements'))
#     except Exception as e:
#         db.session.rollback()
#         flash('Erreur lors de la suppression du mouvement', 'error')
#         return redirect(url_for('mouvements'))

@app.route('/alertes')
@login_required
def alertes():
    alertes = db.session.query(Alerte, Produit).join(Produit).filter(Alerte.statut == 'NOUVELLE').all()
    alertes_traites = db.session.query(Alerte, Produit).join(Produit).filter(Alerte.statut == 'TRAITEE').count()
    alertes_nouvelles = Alerte.query.filter_by(statut='NOUVELLE').count()
    return render_template('alerte/alertes.html', alertes=alertes, alertes_traites=alertes_traites, alertes_nouvelles=alertes_nouvelles)

@app.route('/api/stats')
@login_required
def api_stats():
    # Catégories avec nombre de produits
    categories_stats = db.session.query(
        Categorie.nom,
        func.count(Produit.id_produit)
    ).join(Produit).group_by(Categorie.id_categorie).all()

    # Récupération des mouvements des 7 derniers jours
    mouvements_recent = db.session.query(
        func.date(Mouvement.date_mouvement),
        Mouvement.type_mouvement,
        func.count(Mouvement.id_mouvement)
    ).filter(
        Mouvement.date_mouvement >= datetime.utcnow() - timedelta(days=7)
    ).group_by(
        func.date(Mouvement.date_mouvement),
        Mouvement.type_mouvement
    ).all()

    # Organisation des mouvements pour l’API
    mouvements_data = []
    mouvements_dict = {}

    # Initialiser toutes les dates sur 7 jours avec 0
    for i in range(7):
        date_str = (datetime.utcnow() - timedelta(days=6 - i)).strftime("%Y-%m-%d")
        mouvements_dict[date_str] = {"date": date_str, "entrees": 0, "sorties": 0}

    # Remplir avec les données réelles
    for date_mvt, type_mvt, count in mouvements_recent:
        date_str = date_mvt.strftime("%Y-%m-%d")
        if type_mvt.lower() == "entrée":
            mouvements_dict[date_str]["entrees"] = count
        elif type_mvt.lower() == "sortie":
            mouvements_dict[date_str]["sorties"] = count

    mouvements_data = list(mouvements_dict.values())

    return jsonify({
        "categories": [{"name": cat, "value": count} for cat, count in categories_stats],
        "total_produits": Produit.query.count(),
        "stock_total": db.session.query(func.sum(Produit.stock_actuel)).scalar() or 0,
        "mouvements": mouvements_data
    })

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # Créer un utilisateur admin par défaut
        admin = Utilisateur.query.filter_by(login='admin').first()
        if not admin:
            admin = Utilisateur(
                nom='Administrateur',
                login='admin',
                mot_de_passe=generate_password_hash('admin123$'),
                role='ADMIN'
            )
            db.session.add(admin)
            db.session.commit()
    
    app.run(debug=True)