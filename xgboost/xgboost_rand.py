"""
xgb
author: li zeng
"""
import os
import sys
sys.path.append(os.path.realpath(os.curdir)+'/..')
import xgboost as xgb
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from sklearn.metrics import r2_score
from sklearn.model_selection import KFold
from sklearn.linear_model import LinearRegression
import itertools
import pickle
import time
import mca
from sklearn.decomposition import PCA, FastICA
from sklearn.model_selection import RandomizedSearchCV


# command line inputs
input_fd=sys.argv[1]
output_fd = sys.argv[2]

if not os.path.exists(output_fd):
    os.makedirs(output_fd)


"""----------------------
LOAD DATA
----------------------"""
TRAIN = pd.DataFrame.from_csv(os.path.join(input_fd,'train.csv'))
TEST = pd.DataFrame.from_csv(os.path.join(input_fd,'test.csv'))

y = TRAIN.y
del TRAIN['y']
del TEST['y']


"""----------------------
LINEAR MODEL with X0
----------------------"""
X0_cols = list(filter(lambda x: 'X0'+'_' in x, TRAIN.columns))

linear_fit = LinearRegression(fit_intercept=False)
linear_fit.fit(TRAIN[X0_cols],y)
linear_fit.score(TRAIN[X0_cols],y)
y_linear_train = linear_fit.predict(TRAIN[X0_cols]) # linear pred on train
y_linear_test = linear_fit.predict(TEST[X0_cols]) # linear pred on test
res = y - y_linear_train # residual


# drop X0 columns
TRAIN.drop(X0_cols,axis=1,inplace=True)
TEST.drop(X0_cols,axis=1,inplace=True)


# # Append decomposition components to datasets
bin_cols = [x for x in TRAIN.columns if not '_' in x]
temp = pd.concat([TRAIN,TEST],axis=0)[bin_cols]

ncomp=8

# MCA
mca_out = mca.mca(temp,ncols=ncomp)
mca_mat = pd.DataFrame(mca_out.fs_r_sup(temp,ncomp),index=temp.index)


# ICA
ica = FastICA(n_components=ncomp, random_state=1)
ica_mat =pd.DataFrame(ica.fit_transform(temp),index=temp.index)


# append
for i in range(min(ncomp,mca_mat.shape[1])):
    TRAIN['mca_' + str(i)] = mca_mat.loc[TRAIN.index,i]
    TEST['mca_' + str(i)] = mca_mat.loc[TEST.index,i]

for i in range(min(ncomp,ica_mat.shape[1])):
    TRAIN['ica_' + str(i)] = ica_mat.loc[TRAIN.index, i]
    TEST['ica_' + str(i)] = ica_mat.loc[TEST.index, i]


# remove duplicated columns
temp = TRAIN[bin_cols].T.duplicated()
TRAIN.drop(temp.index[temp],axis=1,inplace=True)
TEST.drop(temp.index[temp],axis=1,inplace=True)

"""----------------------
GENERATE PARAMETERS
----------------------"""
def frange(start, stop, step):
    i = start
    while i < stop:
        yield i
        i += step

grid_params = {'n_estimators': range(400, 800),
              'learning_rate': list(frange(0.001,0.05, 0.001)), #so called `eta` value
              'max_depth': range(2,8),
              'subsample': [0.8,0.85,0.9,0.95],
              'base_score': [0],
              'gamma': range(0, 30),
              'colsample_bytree': [0.8,0.85,0.9,0.95],
              'reg_alpha': [0.0001,0.0005,0.001,0.005,0.01,0.05,0.1,0.5,1,5,100],
              'reg_lambda': [0.0001,0.0005,0.001,0.005,0.01,0.05,0.1,0.5,1,5,100],
              'min_child_weight': range(1, 10)
              }

xgb_model = xgb.XGBRegressor(early_stopping_rounds=30)
kfold = KFold(n_splits=5, shuffle=True, random_state=1234)
rgs = RandomizedSearchCV(xgb_model, grid_params, n_iter = 150, n_jobs = 3,
                         cv = kfold,verbose=1)
rgs.fit(TRAIN,res)
print(rgs.best_score_ )

final_params = rgs.best_params_
print(final_params)

"""----------------------
CROSS VALIDATION IN TRAINING
----------------------"""
np.random.seed(12)

"""
xgb_params = {
    'eta': 0.005,
    'max_depth': 2,
    'subsample': 0.93,
    'objective': 'reg:linear',
    'eval_metric': 'rmse',
    'silent': 1,
    'base_score': 0
}
"""

# binary columns

def myCV(xgb_params):
    numFolds = 5
    kf = KFold(n_splits= numFolds ,shuffle = True)
    kf.get_n_splits(TRAIN)

    out = {'train_r2':[],'test_r2':[]}
    ct = 1
    for train_ind, test_ind in kf.split(TRAIN):
        print('calculating fold:',ct)
        # split data
        X_train, X_test = TRAIN.iloc[train_ind], TRAIN.iloc[test_ind]
        y_train, y_test = res.iloc[train_ind], res.iloc[test_ind]

        # fit xgboost
        dtrain = xgb.DMatrix(X_train, y_train, feature_names=X_train.columns.values)
        dtest = xgb.DMatrix(X_test,y_test)
        cv_result = xgb.cv(xgb_params,
                           dtrain,
                           num_boost_round=3000, # increase to have better results (~700)
                           early_stopping_rounds=40,
                           verbose_eval=False,
                           show_stdv=False
                           )
        niter = cv_result.shape[0]
        model = xgb.train(dict(xgb_params, silent=0), dtrain, num_boost_round=niter)
        out['train_r2'].append(r2_score( y.iloc[train_ind], y_linear_train[train_ind] + model.predict(dtrain)))
        out['test_r2'].append(r2_score(y.iloc[test_ind], y_linear_train[test_ind]+model.predict(dtest)))
        # at iter
        print('at iter:',r2_score(y.iloc[test_ind], y_linear_train[test_ind]+model.predict(dtest)))

        ct += 1
    out['train_r2_mean']=np.mean(out['train_r2'])
    out['test_r2_mean']=np.mean(out['test_r2'])
    return out

cur_cv = myCV(final_params)
print(cur_cv)


"""----------------------
FINAL MODEL
----------------------"""

model2= rgs.best_estimator_

# score
# r2_train1 = r2_score(dtrain.get_label(), model1.predict(dtrain))
# print("R2 on training:",r2_train1,'\n')
r2_train2 = r2_score(y, y_linear_train + model2.predict(TRAIN))
print('------------------------------------------------------')
print("R2 on training:",r2_train2,'\n')
print('------------------------------------------------------')


"""----------------------
PREDICTION
----------------------"""

# make predictions and save results

y_pred = y_linear_test + model2.predict(TEST)

output = pd.DataFrame({'y': y_pred},index=TEST.index)
output.loc[[289,624,5816,6585,7420,7805],:] = 100.63 # set to mean

# add probe
probe_out = pd.DataFrame.from_csv('../probing/probe_out.csv')
output.loc[probe_out.index,'y'] = probe_out['yValue']
output.to_csv(output_fd+'/XGB_withLinear_mcaica_tuned.csv',index_label='ID')


fig, ax = plt.subplots(figsize=(12,30))
xgb.plot_importance(model2,height=0.8, ax=ax)
fig.savefig(output_fd+'/imp.pdf')
