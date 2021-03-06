from matplotlib.patches import Circle, Ellipse, Polygon
import numpy as np
from scipy.interpolate import griddata
import scipy.io
from scipy import signal
from sklearn.cross_decomposition import CCA
from data_preprocessing import *
from data_loader import load_data
import warnings
from metrics import *
import os
import glob
import h5py
import numpy as np
import mne
import scipy.io
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_curve, auc
import tensorflow as tf
from keras.utils.np_utils import to_categorical
from sklearn.preprocessing import label_binarize

def eventDuration(): 
    for i_class in range(n_classes):
        up = np.where(rise[:, i_class])[0]
        down = np.where(fall[:, i_class])[0]
        if up.size > down.size:
            down = np.append(down, V.shape[0])
        durations = down - up
        unique_durations = np.unique(durations)  
        for i in range(nrOfEvents):
            E[up, i, i_class] = durations == unique_durations[i]

# Generates E for the simple event-type
def eventSimple():
    for i_class in range(n_classes):
        for i in range(1800):
            for j in range(90):
                if not i%10 == 0:
                    E[i, 0, j] = 0

# Generates E for the contrast event-type
def eventContrast():
    for i_class in range(n_classes):
        up = np.where(rise[:, i_class])[0]
        down = np.where(fall[:, i_class])[0]   
        E[up, 0, i_class] = True
        E[down, 1, i_class] = True

def train_cca(dataset,mode,model, X_train, yt_train,n_subjects,n_classes):

    warnings.filterwarnings("ignore")
    results = {}
    if(dataset=='8_channel_cVEP'):
        n_subjects = 30
        n_classes = 20
        mat = scipy.io.loadmat('./datasets/8_channel_cVEP/resources/mgold_61_6521_flip_balanced_20.mat')
        codes = mat['codes'].astype('float32')
        codebook = np.moveaxis(codes,1,0).astype('float32')

    elif(dataset=='256_channel_cVEP'):
        n_subjects = 5
        n_classes = 36
        codebook = np.load('./datasets/256_channel_cVEP/Scripts/codebook_36t.npy')[:n_classes]
        codes = np.moveaxis(codebook,1,0)
    else:
        warnings.warn("Unsupported dataset")

    V = codes
    V = V.astype("bool_")
    Vr = np.roll(V, 1, axis=0)
    Vr[0, :] = False
    rise = np.logical_and(V, np.logical_not(Vr)).astype("uint8")
    fall = np.logical_and(np.logical_not(V), Vr).astype("uint8")

    nrOfEvents = 2
    n_classes = V.shape[1]
    # Event Matrix (1st event: rise, 2nd event: fall)
    E = np.zeros((V.shape[0], nrOfEvents, V.shape[1]), dtype="uint8")
    for i_class in range(n_classes):
        up = np.where(rise[:, i_class])[0]
        down = np.where(fall[:, i_class])[0]   
        E[up, 0, i_class] = True
        E[down, 1, i_class] = True


    n_samples_transient = int(0.3 * 60)
    M = np.zeros((V.shape[0], 0, V.shape[1]), dtype="uint8")
    for i_event in range(E.shape[1]): # 2 events
        M_event = np.zeros((V.shape[0], n_samples_transient, V.shape[1]), dtype="uint8") # (126, 54, 20)
        M_event[:, 0, :] = E[:, i_event, :] # (126,0,20) <- (126,event,20)

        for i_sample in range(1, n_samples_transient): # (1,54)
            M_event[:, i_sample, :] = np.roll(M_event[:, i_sample-1, :], 1, axis=0) # (126, i_sample, 20) <- rolled i_sample-1
            M_event[0, i_sample, :] = 0 # first bit 0

        M = np.concatenate((M, M_event), axis=1)   

    _,n_samples,n_channels = X_train.shape
                
    M_ = M[:, :, yt_train.astype('int')]
    M_ = M_.transpose((1, 0, 2))
    M_ = np.reshape(M_, (nrOfEvents*n_samples_transient, -1)).T

    X_train = signal.resample(X_train, 126, axis=1) 
    resampled_X = np.moveaxis(X_train, 2,0)
    resampled_X = np.moveaxis(resampled_X, 2,1)
    X_ = np.reshape(resampled_X, (n_channels, -1)).T

    cca = CCA(n_components=1)
    cca.fit(X_.astype("float32"), M_.astype("float32"))

    w = cca.x_weights_.flatten()
    r = cca.y_weights_.flatten()

    weights = []
    weights.append(w)
    weights.append(r)
    weights  = np.array(weights, dtype='object')
    T = np.zeros((M.shape[0], n_classes))
    for i_class in range(n_classes):
        T[:, i_class] = np.dot(M[:, :, i_class], r)          

    return T, weights

def evaluate_cca(dataset,mode,model,X_test,yt_test,T,n_subjects,n_classes,weights):
    w = weights[0]
    results = {}
    X_test = signal.resample(X_test, 126, axis=1) 
    resampled_X_test = np.moveaxis(X_test, 2,0)
    resampled_X_test  = np.moveaxis(resampled_X_test, 2,1)


    X_filtered = np.zeros((resampled_X_test.shape[1], yt_test.size))
    for i_trial in range(yt_test.size):
        X_filtered[:, i_trial] = np.dot(w, resampled_X_test[:, :, i_trial])

    prediction = np.zeros(yt_test.size)
    for i_trial in range(yt_test.size):
        rho = np.corrcoef(X_filtered[:, i_trial], T.T)[0, 1:]
        prediction[i_trial] = np.argmax(rho)

    category_accuracy = (np.sum(prediction == yt_test)/len(prediction))
    results['category_accuracy'] = np.array(category_accuracy)

    accuracy = category_accuracy
    num_trials = X_test.shape[0]
    time_min = (X_test.shape[0]* 126*(2.1/126)*(1/60))
    itr = calculate_ITR(n_classes, accuracy, time_min, num_trials)
    results['ITR'] = np.array(itr)

    precision = precision_score(yt_test, prediction, average='weighted')
    recall = recall_score(yt_test, prediction, average='weighted')
    f1 = f1_score(yt_test, prediction, average='weighted')

    cm_c = confusion_matrix(yt_test, prediction)

    fpr = dict()
    tpr = dict()
    roc_auc = dict()

    yt_test_categorical = label_binarize(yt_test, classes = np.arange(0,n_classes))
    prediction_categorical = label_binarize(prediction,classes = np.arange(0,n_classes))
    for n in range(n_classes):

        fpr[n], tpr[n], _ = roc_curve(yt_test_categorical[:, n], prediction_categorical[:, n])
        roc_auc[n] = auc(fpr[n], tpr[n])

    results['recall'] = np.array(recall)
    results['precision'] = np.array(precision)
    results['f1_score'] = np.array(f1)
    results['category_cm'] = np.array(cm_c)
    results['fpr'] = fpr
    results['tpr'] = tpr
    results['auc'] = roc_auc

    if(mode!='cross_subject'):
        acc_time_step =[]
        acc_time_step_r =[]
        itr_time_step = []
        pred_time_step = np.zeros((X_test.shape[0],X_test.shape[1],n_classes))
        pred_time_step_r = np.zeros((X_test.shape[0],X_test.shape[1],n_classes))
        for k in range(0, X_test.shape[1]):
            X_test_new = X_test[:,:k,:] 
            
            resampled_X_test = np.moveaxis(X_test_new, 2,0)
            resampled_X_test  = np.moveaxis(resampled_X_test, 2,1)


            X_filtered = np.zeros((resampled_X_test.shape[1], yt_test.size))
            for i_trial in range(yt_test.size):
                X_filtered[:, i_trial] = np.dot(w, resampled_X_test[:, :, i_trial])

            prediction = np.zeros(yt_test.size)
            rho_all = np.zeros((yt_test.size,n_classes))
            for i_trial in range(yt_test.size):
                T_new = T.T
                T_new = T_new[:,:k]
                rho = np.corrcoef(X_filtered[:, i_trial], T_new)[0, 1:]
                prediction[i_trial] = np.argmax(rho)
                rho_all[i_trial] = rho
                
            acc = (np.sum(prediction == yt_test)/len(prediction))
            acc_time_step.append(acc)

            
            accuracy = acc
            num_trials = X_test_new.shape[0]
            time_min = (X_test_new.shape[0]* k*(2.1/126)*(1/60))
            itr = calculate_ITR(n_classes, accuracy, time_min, num_trials)
            itr_time_step.append(itr)


            pred_time_step[:,k] = rho_all
            
    #     for k in range(0, X_test.shape[1]):
    #         X_test_new = X_test.copy()
    #         r = 126-k-1
    #         X_test_new = X_test[:,r:,:] 

    #         resampled_X_test = np.moveaxis(X_test_new, 2,0)
    #         resampled_X_test  = np.moveaxis(resampled_X_test, 2,1)


    #         X_filtered = np.zeros((resampled_X_test.shape[1], yt_test.size))
    #         for i_trial in range(yt_test.size):
    #             X_filtered[:, i_trial] = np.dot(w, resampled_X_test[:, :, i_trial])

    #         prediction = np.zeros(yt_test.size)
    #         rho_all = np.zeros((yt_test.size,n_classes))
    #         for i_trial in range(yt_test.size):
    #             T_new = T.T
    #             T_new = T_new[:,r:]
    #             rho = np.corrcoef(X_filtered[:, i_trial], T_new)[0, 1:]
    #             prediction[i_trial] = np.argmax(rho)
    #             rho_all[i_trial] = rho
            
    #         acc = 100*(np.sum(prediction == yt_test)/len(prediction))
    #         acc_time_step_r.append(acc)


    #         pred_time_step_r[:,k] = rho_all
        

        results['variable_time_steps'] = np.array(acc_time_step)
        #results['variable_time_steps_r'] = np.array(acc_time_step_r)
        results['ITR_time_steps'] = np.array(itr_time_step)

        results['pred_time_step'] = pred_time_step
        #results['pred_time_step_r'] = pred_time_step_r

    return results


def run_cca(dataset,mode,model):            
    filename = "./results/{}/{}/{}/{}_{}_run.txt".format(model,dataset,mode,model,mode)
    os.makedirs(os.path.dirname(filename), exist_ok=True)           
    run_f = open(filename, "w")
    if(mode=='cross_subject'):
        results = {}
        with open('./datasets/{}.pickle'.format(dataset), 'rb') as handle:
            data = pickle.load(handle)

        X = data['X']
        Ys = data['Ys']
        Yt = data['Yt'] 

        if(dataset=='8_channel_cVEP'):
            n_subjects = 30
            n_classes = 20
            mat = scipy.io.loadmat('./datasets/8_channel_cVEP/resources/mgold_61_6521_flip_balanced_20.mat')
            codes = mat['codes'].astype('float32')
            codebook = np.moveaxis(codes,1,0).astype('float32')

            X = np.reshape(X[:,:100],(30,100*15,504,8))
            Ys = Ys[:,:100]
            Yt = Yt[:,:100]

            Ys = np.repeat(Ys,15,axis=1)
            Yt = np.repeat(Yt,15,axis=1)

        if(dataset=='256_channel_cVEP'):
            n_subjects = 5
            n_classes = 36
            codebook = np.load('./datasets/256_channel_cVEP/Scripts/codebook_36t.npy')[:n_classes]
            codes = np.moveaxis(codebook,1,0)

            X, rejected_chans = remove_bad_channels(X)

            X = np.reshape(X,(5,108*2,504,256))

            Ys = np.repeat(Ys,2,axis=1)
            Yt = np.repeat(Yt,2,axis=1)


        # Preprocessing data
        low_cutoff = 2
        high_cutoff = 30
        sfreq = 240
        X = bandpass_filter_data(X, low_cutoff, high_cutoff, sfreq)
        
        for i in range(0,n_subjects):
            results[i+1] = {}

            X_new = X[i]
            ys_new = Ys[i]
            yt_new = Yt[i]

            y_new= np.concatenate((yt_new[..., np.newaxis],ys_new), axis=1)

            X_train, X_val, y_train, y_val = train_test_split(X_new, y_new, test_size=0.2,stratify=y_new[:,0], shuffle= True)

            X_train = standardize_data(X_train)
            X_val = standardize_data(X_val)

            ys_train = y_train[:,1:]
            ys_val = y_val[:,1:]

            yt_train = y_train[:,0]
            yt_val = y_val[:,0]

            if(dataset == '256_channel_cVEP'):
                X_train, ys_train, yt_train = augment_data(X_train, ys_train, yt_train)

            T, weights = train_cca(dataset,mode,model, X_train, yt_train,n_subjects, n_classes)

            filename = './saved_models/{}/{}/{}/{}.pickle'.format(model,dataset,mode,model,i+1)
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            with open(filename, 'wb') as handle:
                pickle.dump(weights, handle, protocol=pickle.HIGHEST_PROTOCOL)

            for j in range(0,n_subjects):
                results[i+1][j+1] = {}
                if(j!=i):
                    X_test = X[j]
                    X_test = standardize_data(X_test)
                    ys_test = Ys[j]
                    yt_test= Yt[j]
                else:
                    X_test = X_val
                    ys_test = ys_val
                    yt_test = yt_val

                if (dataset == '256_channel_cVEP'):
                    X_test = X_test
                    ys_test = ys_test
                    yt_test = yt_test

                results_eval = evaluate_cca(dataset,mode,model,X_test,yt_test,T,n_subjects,n_classes, weights)
                print("Train on subject {} test on subject {} category_accuracy: {}".format(i+1,j+1,results_eval['category_accuracy']))
                print("Train on subject {} test on subject {} category_accuracy: {}".format(i+1,j+1,results_eval['category_accuracy']),file=run_f)
                results[i+1][j+1]['category_accuracy'] = results_eval['category_accuracy']
                results[i+1][j+1]['ITR'] = results_eval['ITR']
                results[i+1][j+1]['category_cm'] =  results_eval['category_cm']

                results[i+1][j+1]['recall'] = results_eval['recall']
                results[i+1][j+1]['precision'] = results_eval['precision']
                results[i+1][j+1]['f1_score'] = results_eval['f1_score']
                results[i+1][j+1]['fpr'] = results_eval['fpr']
                results[i+1][j+1]['tpr'] = results_eval['tpr']
                results[i+1][j+1]['auc'] = results_eval['auc']
                if(mode!='cross_subject'):
                    results[i+1][j+1]['variable_time_steps'] = results_eval['variable_time_steps']
                #     results[i+1][j+1]['variable_time_steps_r'] = results_eval['variable_time_steps_r']
                #results[i+1][j+1]['ITR_time_steps'] = results_eval['ITR_time_steps']

                #results[i+1][j+1]['pred_time_step'] = results_eval['pred_time_step']
                #     results[i+1][j+1]['pred_time_step_r'] = results_eval['pred_time_step_r']

        filename = './results/{}/{}/{}/{}_{}.pickle'.format(model,dataset,mode,model,mode)
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'wb') as handle:
            pickle.dump(results, handle, protocol=pickle.HIGHEST_PROTOCOL)


    elif(mode=='within_subject' or mode=='loso_subject'):
        results = {}
        if(dataset=='8_channel_cVEP'):
            n_subjects = 30
            n_classes = 20
            n_folds = 5

        if(dataset=='256_channel_cVEP'):
            n_subjects = 5
            n_classes = 36
            n_folds = 3

        for i in range(0,n_subjects):
            results[i+1] = {}
            for fold in range(0,n_folds):
                results[i+1][fold+1] = {}
                
                data = load_data(mode,dataset,model,i,fold)
                X_train = data['X_train']
                X_val = data['X_val']
                X_test = data['X_test']

                ys_train = data['ys_train']
                ys_val = data['ys_val']
                ys_test = data['ys_test']

                yt_train = data['yt_train']
                yt_val = data['yt_val']
                yt_test = data['yt_test']

                if (dataset == '256_channel_cVEP'):
                    X_test = X_test
                    ys_test = ys_test
                    yt_test = yt_test

                if (len(yt_train.shape)==2):
                    yt_train = np.argmax(yt_train,axis=1)
                    yt_val = np.argmax(yt_val,axis=1)
                    yt_test = np.argmax(yt_test,axis=1)

                _,n_samples,n_channels = X_train.shape

                if(mode == 'within_subject'):
                    T, weights = train_cca(dataset,mode,model, X_train, yt_train,n_subjects, n_classes)

                else:
                    if(fold==0):
                        T, weights = train_cca(dataset,mode,model, X_train, yt_train,n_subjects, n_classes)
                
                results_eval = evaluate_cca(dataset,mode,model,X_test,yt_test,T,n_subjects,n_classes,weights)

                print("Subject {} fold {} category_accuracy: {}".format(i+1,fold+1,results_eval['category_accuracy']))
                print("Subject {} fold {} category_accuracy: {}".format(i+1,fold+1,results_eval['category_accuracy']),file=run_f)
                results[i+1][fold+1]['category_accuracy'] = results_eval['category_accuracy']
                results[i+1][fold+1]['ITR'] = results_eval['ITR']
                results[i+1][fold+1]['category_cm'] =  results_eval['category_cm']

                results[i+1][fold+1]['recall'] = results_eval['recall']
                results[i+1][fold+1]['precision'] = results_eval['precision']
                results[i+1][fold+1]['f1_score'] = results_eval['f1_score']
                results[i+1][fold+1]['fpr'] = results_eval['fpr']
                results[i+1][fold+1]['tpr'] = results_eval['tpr']
                results[i+1][fold+1]['auc'] = results_eval['auc']

                filename = './saved_models/{}/{}/{}/S{}_f{}.pickle'.format(model,dataset,mode,i+1,fold+1)
                os.makedirs(os.path.dirname(filename), exist_ok=True)
                with open(filename, 'wb') as handle:
                    pickle.dump(weights, handle, protocol=pickle.HIGHEST_PROTOCOL)
                
                #results[i+1][fold+1]['category_accuracy'] = results_eval['category_accuracy']
                results[i+1][fold+1]['variable_time_steps'] = results_eval['variable_time_steps']
                # results[i+1][fold+1]['variable_time_steps_r'] = results_eval['variable_time_steps_r']
                results[i+1][fold+1]['ITR_time_steps'] = results_eval['ITR_time_steps']

                results[i+1][fold+1]['pred_time_step'] = results_eval['pred_time_step']
                # results[i+1][fold+1]['pred_time_step_r'] = results_eval['pred_time_step_r']
                    
        filename = './results/{}/{}/{}/{}_{}.pickle'.format(model,dataset,mode,model,mode)
        os.makedirs(os.path.dirname(filename), exist_ok=True)           
        with open(filename, 'wb') as handle:
                pickle.dump(results, handle, protocol=pickle.HIGHEST_PROTOCOL)

    else:
        warnings.warn("Unsupported mode")

# datasets = ['8_channel_cVEP','256_channel_cVEP']
# modes = ['within_subject','loso_subject','cross_subject']
# model = "cca"

# for dataset in datasets:
#     for mode in modes: 
#         print('\n------Running {} for dataset {} in mode {}-----\n'.format(model, dataset, mode))
#         run_cca(dataset, mode, model)